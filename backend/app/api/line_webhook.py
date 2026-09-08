import base64
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.models.entities import ItemReport, User
from app.schemas.reports import ReportCreate
from app.services.line import (
    LineClient,
    build_match_messages,
    build_match_reply,
    parse_report_kind,
)
from app.services.reports import ReportService


router = APIRouter(prefix="/webhooks", tags=["line"])


def _public_base_url(request: Request) -> str:
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    scheme = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    return f"{scheme}://{host}".rstrip("/")


async def _candidate_messages(request: Request, session: AsyncSession, report: ItemReport, matches) -> list[dict]:
    image_url = None
    if matches:
        found = await session.scalar(
            select(ItemReport)
            .options(selectinload(ItemReport.images))
            .where(ItemReport.id == matches[0].found_report_id)
        )
        if found and found.images:
            image_url = f"{_public_base_url(request)}/api/v1/reports/{found.id}/image"
    text = build_match_reply(report.id, report.kind, matches, has_image=bool(image_url))
    return build_match_messages(text, image_url)


async def _latest_lost_report(session: AsyncSession, line_user_id: str | None) -> ItemReport | None:
    if not line_user_id:
        return None
    user = await session.scalar(select(User).where(User.line_user_id == line_user_id))
    if not user:
        return None
    return await session.scalar(
        select(ItemReport)
        .options(selectinload(ItemReport.embedding), selectinload(ItemReport.images))
        .where(ItemReport.user_id == user.id, ItemReport.kind == "lost")
        .order_by(ItemReport.created_at.desc())
        .limit(1)
    )


def _is_list_request(text: str) -> bool:
    asks_to_list = any(term in text for term in ("列出", "列目前", "清單", "有哪些", "目前有"))
    mentions_items = any(term in text for term in ("遺失物", "拾獲物", "撿到", "遺失通報", "報失"))
    return asks_to_list and mentions_items


async def _list_items_reply(service: ReportService, text: str) -> str:
    kind = "lost" if any(term in text for term in ("遺失通報", "報失案件")) else "found"
    reports = [item for item in await service.list_reports(limit=20, kind=kind) if item.status == "open"][:5]
    label = "遺失通報" if kind == "lost" else "目前拾獲物"
    if not reports:
        return f"目前還沒有開放中的{label}。"
    lines = [f"{label}共有以下 {len(reports)} 筆（顯示最新資料）："]
    for index, item in enumerate(reports, 1):
        where = f"｜{item.location}" if item.location else ""
        when = item.occurred_at.strftime("%m/%d %H:%M") if item.occurred_at else item.created_at.strftime("%m/%d %H:%M")
        photo = "｜有照片" if item.images else ""
        lines.append(f"{index}. {item.description}{where}｜{when}{photo}")
    lines.append("想查看最相似候選的照片，可以直接說「給我照片」。")
    return "\n".join(lines)


def _looks_like_item_detail(text: str) -> bool:
    terms = (
        "飲料", "瓶", "耳機", "手機", "錢包", "鑰匙", "卡", "雨傘", "背包",
        "黑", "白", "藍", "紅", "綠", "灰", "粉", "圖書館", "教室", "樓",
        "貼紙", "刮痕", "刻字", "保護殼", "吊飾",
    )
    return len(text) <= 60 and any(term in text for term in terms)


@router.post("/line")
async def line_webhook(
    request: Request,
    x_line_signature: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    body = await request.body()
    client = LineClient(settings)
    if not client.verify_signature(body, x_line_signature):
        raise HTTPException(401, "Invalid LINE signature")

    payload = json.loads(body or b"{}")
    service = ReportService(session, settings)
    for event in payload.get("events", []):
        if event.get("type") != "message":
            continue
        message = event.get("message", {})
        source = event.get("source", {})
        line_user_id = source.get("userId")
        reply_token = event.get("replyToken", "")

        if message.get("type") == "text":
            raw_text = message.get("text", "").strip()
            if _is_list_request(raw_text):
                await client.reply(reply_token, await _list_items_reply(service, raw_text))
                continue
            if any(term in raw_text for term in ("給我看", "看照片", "給我照片", "傳照片", "照片給我", "再看一次")):
                latest = await _latest_lost_report(session, line_user_id)
                if not latest:
                    await client.reply(reply_token, "我還找不到你最近的遺失案件。你可以先告訴我遺失了什麼，例如：我的黑色 AirPods 在圖書館不見了。")
                    continue
                recent_matches = await service.list_matches(latest.id)
                await client.reply(reply_token, await _candidate_messages(request, session, latest, recent_matches))
                continue
            if raw_text in ("這可能是我的", "可能是我的", "是我的"):
                await client.reply(
                    reply_token,
                    "太好了！為了避免冒領，請打開認領頁並提供只有失主知道的特徵，例如刻字、刮痕或保護殼細節。\n"
                    f"{_public_base_url(request)}/mobile",
                )
                continue
            if raw_text in ("這不是我的", "不是我的", "不是"):
                await client.reply(reply_token, "了解，謝謝你幫忙確認。你可以再補充顏色、品牌、地點或照片，我會用新線索重新比對。")
                continue
            if raw_text.startswith(("http://", "https://")):
                await client.reply(reply_token, "這是 LostLink AI 的手機入口。打開後可以拍照登記拾獲物，或用文字與照片尋找遺失物。")
                continue
            kind, description = parse_report_kind(raw_text)
            if not kind:
                latest = await _latest_lost_report(session, line_user_id)
                if latest and _looks_like_item_detail(raw_text):
                    report, matches = await service.refine(latest, raw_text)
                    messages = await _candidate_messages(request, session, report, matches)
                    messages[0]["text"] = f"收到，我已把「{raw_text}」加入協尋線索。\n" + messages[0]["text"]
                    await client.reply(reply_token, messages)
                    continue
                await client.reply(
                    reply_token,
                    "我可以幫你找遺失物，也可以登記撿到的東西。直接用平常說話的方式告訴我就好，例如：\n"
                    "「我的黑色 AirPods 在圖書館不見了」\n"
                    "「我在二樓撿到一把藍色雨傘」\n也可以直接傳一張拾獲物照片給我。",
                )
                continue
            report, matches = await service.create(
                ReportCreate(
                    kind=kind,
                    description=description,
                    line_user_id=line_user_id,
                )
            )
        elif message.get("type") == "image":
            try:
                image = await client.download_content(message["id"])
            except RuntimeError:
                await client.reply(reply_token, "尚未設定 LINE Access Token。")
                continue
            report, matches = await service.create(
                ReportCreate(
                    kind="found",
                    description="拾獲者上傳的物品照片",
                    image_base64=base64.b64encode(image).decode("ascii"),
                    line_user_id=line_user_id,
                )
            )
        else:
            continue

        await client.reply(reply_token, await _candidate_messages(request, session, report, matches))

    return {"status": "ok"}
