import base64
import json
import time

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
    extract_location,
    parse_report_kind,
)
from app.services.ollama import OllamaService
from app.services.reports import ReportService


router = APIRouter(prefix="/webhooks", tags=["line"])
PENDING_TTL_SECONDS = 10 * 60
MAX_PENDING_USERS = 50
_pending_images: dict[str, tuple[float, bytes]] = {}
_pending_intents: dict[str, tuple[float, str, str]] = {}
_active_modes: dict[str, tuple[float, str]] = {}
_chat_histories: dict[str, tuple[float, list[dict[str, str]]]] = {}
_recent_searches: dict[str, tuple[float, str]] = {}


def _cleanup_pending() -> None:
    cutoff = time.monotonic() - PENDING_TTL_SECONDS
    for storage in (
        _pending_images,
        _pending_intents,
        _active_modes,
        _chat_histories,
        _recent_searches,
    ):
        expired = [key for key, value in storage.items() if value[0] < cutoff]
        for key in expired:
            storage.pop(key, None)
        while len(storage) > MAX_PENDING_USERS:
            oldest = min(storage, key=lambda key: storage[key][0])
            storage.pop(oldest, None)


def _generic_description(description: str) -> bool:
    normalized = description.strip(" ：:，,。")
    if normalized in {"", "這個", "東西", "物品", "一個東西", "一個物品"}:
        return True
    # A location plus only a generic noun is not enough to create a useful report.
    # This intentionally avoids maintaining a closed list of supported item names.
    return any(term in normalized for term in ("有東西", "某個東西", "某樣東西", "有物品"))


def _store_pending_image(line_user_id: str, image: bytes) -> None:
    _cleanup_pending()
    _pending_images[line_user_id] = (time.monotonic(), image)


def _take_pending_image(line_user_id: str | None) -> bytes | None:
    _cleanup_pending()
    if not line_user_id:
        return None
    value = _pending_images.pop(line_user_id, None)
    return value[1] if value else None


def _store_pending_intent(line_user_id: str, kind: str, description: str) -> None:
    _cleanup_pending()
    _pending_intents[line_user_id] = (time.monotonic(), kind, description)


def _take_pending_intent(line_user_id: str | None) -> tuple[str, str] | None:
    _cleanup_pending()
    if not line_user_id:
        return None
    value = _pending_intents.pop(line_user_id, None)
    return (value[1], value[2]) if value else None


def _store_active_mode(line_user_id: str, kind: str) -> None:
    _cleanup_pending()
    _active_modes[line_user_id] = (time.monotonic(), kind)


def _get_active_mode(line_user_id: str | None) -> str | None:
    _cleanup_pending()
    if not line_user_id:
        return None
    value = _active_modes.get(line_user_id)
    return value[1] if value else None


def _clear_active_mode(line_user_id: str | None) -> None:
    if not line_user_id:
        return
    _active_modes.pop(line_user_id, None)
    _pending_intents.pop(line_user_id, None)
    _pending_images.pop(line_user_id, None)


def _chat_history(line_user_id: str) -> list[dict[str, str]]:
    _cleanup_pending()
    value = _chat_histories.get(line_user_id)
    return list(value[1]) if value else []


def _save_chat_turn(line_user_id: str, user_text: str, assistant_text: str) -> None:
    history = _chat_history(line_user_id)
    history.extend(
        [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ]
    )
    _chat_histories[line_user_id] = (time.monotonic(), history[-8:])


def _mode_menu_message(text: str = "請先選擇你要辦理的項目：") -> list[dict]:
    return [
        {
            "type": "text",
            "text": text,
            "quickReply": {
                "items": [
                    {
                        "type": "action",
                        "action": {
                            "type": "postback",
                            "label": "我遺失物品",
                            "data": "action=start_lost",
                            "displayText": "我有東西不見了",
                        },
                    },
                    {
                        "type": "action",
                        "action": {
                            "type": "postback",
                            "label": "我撿到物品",
                            "data": "action=start_found",
                            "displayText": "我撿到東西了",
                        },
                    },
                    {
                        "type": "action",
                        "action": {
                            "type": "postback",
                            "label": "一般聊天",
                            "data": "action=continue_chat",
                            "displayText": "繼續聊天",
                        },
                    },
                ]
            },
        }
    ]


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
    normalized = text.strip()
    direct_item_question = (
        len(normalized) <= 40
        and normalized.startswith(("有", "目前有", "現在有", "請問有", "有人", "目前有人"))
        and normalized.endswith(("嗎", "？", "?"))
    )
    return (asks_to_list and mentions_items) or direct_item_question


def _is_search_follow_up(line_user_id: str | None, text: str) -> bool:
    _cleanup_pending()
    if not line_user_id or line_user_id not in _recent_searches:
        return False
    normalized = text.strip()
    return len(normalized) <= 20 and normalized.endswith(("呢", "嗎", "？", "?"))


def _remember_search(line_user_id: str | None, text: str) -> None:
    if line_user_id:
        _recent_searches[line_user_id] = (time.monotonic(), text)


async def _list_items_reply(service: ReportService, text: str) -> str:
    kind = "lost" if any(term in text for term in ("遺失通報", "報失案件")) else "found"
    attributes = await service.analyzer.analyze(text)
    reports = [
        item
        for item in await service.list_reports(limit=100, kind=kind)
        if item.status == "open"
    ]
    if attributes.category and attributes.category != "other":
        equivalent_categories = {
            "bottle": {"bottle", "drink"},
            "drink": {"bottle", "drink"},
        }
        allowed_categories = equivalent_categories.get(
            attributes.category,
            {attributes.category},
        )
        reports = [item for item in reports if item.category in allowed_categories]
    if attributes.color:
        color_matches = [item for item in reports if item.color == attributes.color]
        if color_matches:
            reports = color_matches
    reports = reports[:3]
    label = "遺失通報" if kind == "lost" else "拾獲物"
    if not reports:
        return f"目前沒有找到符合條件的待認領{label}。"
    lines = [f"找到以下 {len(reports)} 個可能符合的{label}："]
    for index, item in enumerate(reports, 1):
        where = f"｜{item.location}" if item.location else ""
        when = item.occurred_at.strftime("%m/%d %H:%M") if item.occurred_at else item.created_at.strftime("%m/%d %H:%M")
        photo = "｜有照片" if item.images else ""
        lines.append(f"{index}. {item.description}{where}｜{when}{photo}")
    lines.append("需要協尋時，請點選下方「我遺失物品」，我會進一步比對照片與特徵。")
    return "\n".join(lines)


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
        event_type = event.get("type")
        source = event.get("source", {})
        line_user_id = source.get("userId")
        reply_token = event.get("replyToken", "")

        if event_type == "postback":
            action = event.get("postback", {}).get("data", "")
            if action not in {
                "action=start_lost",
                "action=start_found",
                "action=continue_chat",
            }:
                await client.reply(reply_token, _mode_menu_message())
                continue
            if not line_user_id:
                await client.reply(reply_token, "無法取得 LINE 使用者資料，請重新開啟聊天室後再試。")
                continue
            if action == "action=continue_chat":
                _clear_active_mode(line_user_id)
                _store_active_mode(line_user_id, "chat")
                await client.reply(
                    reply_token,
                    "已進入一般聊天。接下來的文字與照片都不會建立案件；需要協尋或登記時，再從下方選單選擇即可。",
                )
                continue
            kind = "lost" if action.endswith("lost") else "found"
            _active_modes.pop(line_user_id, None)
            _pending_intents.pop(line_user_id, None)
            _store_active_mode(line_user_id, kind)
            prompt = (
                "已切換為「遺失物協尋」。請描述物品名稱、顏色、可能遺失地點，也可以傳照片。資料完整後才會建立案件。"
                if kind == "lost"
                else "已切換為「拾獲物登記」。請描述撿到的物品與地點，也可以先傳照片。資料完整後才會建立案件。"
            )
            await client.reply(reply_token, prompt)
            continue

        if event_type != "message":
            continue
        message = event.get("message", {})

        if message.get("type") == "text":
            raw_text = message.get("text", "").strip()
            if _is_list_request(raw_text) or _is_search_follow_up(line_user_id, raw_text):
                list_reply = await _list_items_reply(service, raw_text)
                _remember_search(line_user_id, raw_text)
                await client.reply(reply_token, list_reply)
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
            mode = _get_active_mode(line_user_id)
            if not mode:
                await client.reply(
                    reply_token,
                    _mode_menu_message(
                        "這則訊息尚未建立案件。請先從下方選擇「我遺失物品」或「我撿到物品」。"
                    ),
                )
                continue
            if mode == "chat":
                try:
                    reply = await OllamaService(settings).general_chat(
                        raw_text,
                        _chat_history(line_user_id),
                        f"{_public_base_url(request)}/mobile",
                    )
                except Exception:
                    reply = (
                        "本機 AI 暫時無法回應。"
                        "如需協尋或登記拾獲物，請使用下方選單。"
                    )
                if line_user_id:
                    _save_chat_turn(line_user_id, raw_text, reply)
                await client.reply(reply_token, reply)
                continue
            parsed_kind, parsed_description = parse_report_kind(raw_text)
            if parsed_kind and parsed_kind != mode:
                await client.reply(
                    reply_token,
                    _mode_menu_message("你的描述和目前模式不同，請重新選擇正確項目。"),
                )
                _clear_active_mode(line_user_id)
                continue
            kind = mode
            description = parsed_description if parsed_kind else raw_text
            pending_context = _take_pending_intent(line_user_id)
            previous_context = None
            context_location = None
            if pending_context and pending_context[0] == kind:
                previous_context = pending_context[1]
                context_location = extract_location(pending_context[1])
            elif pending_context and line_user_id:
                _store_pending_intent(line_user_id, *pending_context)
            pending_image = _take_pending_image(line_user_id)
            if _generic_description(description) and not pending_image:
                if line_user_id:
                    combined_context = "；".join(
                        value for value in (previous_context, description) if value
                    )
                    _store_pending_intent(line_user_id, kind, combined_context)
                try:
                    clarification = await OllamaService(settings).clarification(
                        kind,
                        description,
                        previous_context,
                        bool(pending_image),
                    )
                except Exception:
                    action = "撿到的物品" if kind == "found" else "遺失物"
                    clarification = (
                        f"了解，你要登記{action}。請再補充物品名稱、顏色、特色或照片；"
                        "資料完整後我才會建立案件。"
                    )
                await client.reply(
                    reply_token,
                    clarification,
                )
                continue
            report, matches = await service.create(
                ReportCreate(
                    kind=kind,
                    description=description,
                    location=context_location or extract_location(description),
                    line_user_id=line_user_id,
                    image_base64=(
                        base64.b64encode(pending_image).decode("ascii")
                        if pending_image
                        else None
                    ),
                )
            )
            _clear_active_mode(line_user_id)
        elif message.get("type") == "image":
            mode = _get_active_mode(line_user_id)
            if mode == "chat":
                await client.reply(
                    reply_token,
                    "照片收到了！如果要用這張照片協尋或登記，請先從下方選單選擇「我遺失物品」或「我撿到物品」。",
                )
                continue
            try:
                image = await client.download_content(message["id"])
            except RuntimeError:
                await client.reply(reply_token, "尚未設定 LINE Access Token。")
                continue
            if not mode:
                if line_user_id:
                    _store_pending_image(line_user_id, image)
                await client.reply(
                    reply_token,
                    _mode_menu_message(
                        "照片收到了！請選擇這是你遺失的物品，還是你撿到的物品。"
                    ),
                )
                continue
            pending_intent = _take_pending_intent(line_user_id)
            if not pending_intent:
                _store_pending_image(line_user_id, image)
                await client.reply(
                    reply_token,
                    "照片收到了！請再告訴我物品名稱、顏色、特色或地點，我就能接著幫你處理。",
                )
                continue
            kind, description = pending_intent
            pending_location = extract_location(description)
            if _generic_description(description):
                description = (
                    "拾獲者上傳的物品照片"
                    if kind == "found"
                    else "使用者上傳的遺失物照片"
                )
            report, matches = await service.create(
                ReportCreate(
                    kind=kind,
                    description=description,
                    location=pending_location or extract_location(description),
                    image_base64=base64.b64encode(image).decode("ascii"),
                    line_user_id=line_user_id,
                )
            )
            _clear_active_mode(line_user_id)
        else:
            continue

        messages = await _candidate_messages(request, session, report, matches)
        if getattr(report, "_was_deduplicated", False):
            if matches:
                messages[0]["text"] = (
                    "這和你最近建立、仍在協尋中的案件相同，因此我沿用原案件，不會重複新增。\n"
                    + messages[0]["text"]
                )
            else:
                messages[0]["text"] = (
                    "這和你最近建立、仍在協尋中的案件相同，因此我沿用原案件，不會重複新增。\n"
                    "目前仍沒有符合的物品；有新的拾獲通報時，我會繼續比對。"
                )
        await client.reply(reply_token, messages)

    return {"status": "ok"}
