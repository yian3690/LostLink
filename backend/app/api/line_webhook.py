import base64
import json
import time
from datetime import datetime, timedelta, timezone

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
    extract_time_hint,
    parse_report_kind,
    time_hint_matches,
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


def _has_pending_image(line_user_id: str | None) -> bool:
    _cleanup_pending()
    return bool(line_user_id and line_user_id in _pending_images)


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


def _found_time_confirmation_message() -> list[dict]:
    return [
        {
            "type": "text",
            "text": "我已收到物品、特徵與地點。請問這是剛剛撿到的嗎？",
            "quickReply": {
                "items": [
                    {"type": "action", "action": {"type": "postback", "label": "是，剛剛撿到", "data": "action=confirm_found_now", "displayText": "是，剛剛撿到的"}},
                    {"type": "action", "action": {"type": "postback", "label": "補充其他時間", "data": "action=provide_found_time", "displayText": "我要補充撿到時間", "inputOption": "openKeyboard"}},
                    {"type": "action", "action": {"type": "postback", "label": "取消登記", "data": "action=continue_chat", "displayText": "取消，繼續聊天"}},
                ]
            },
        }
    ]


async def _found_registered_messages(
    request: Request,
    session: AsyncSession,
    report: ItemReport,
    matches,
) -> list[dict]:
    messages = await _candidate_messages(request, session, report, matches)
    messages[0]["text"] = (
        "感謝提供資訊，我已經收到你提供的資料，拾獲物已完成登記。\n"
        + messages[0]["text"]
    )
    return messages


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
    asks_if_seen = (
        normalized.endswith(("嗎", "？", "?"))
        and any(term in normalized for term in ("有看到", "有找到", "有撿到", "有拾獲"))
    )
    return (asks_to_list and mentions_items) or direct_item_question or asks_if_seen


def _is_search_follow_up(line_user_id: str | None, text: str) -> bool:
    _cleanup_pending()
    if not line_user_id or line_user_id not in _recent_searches:
        return False
    normalized = text.strip()
    return len(normalized) <= 20 and normalized.endswith(("呢", "嗎", "？", "?"))


def _is_complete_item_search(service: ReportService, text: str) -> bool:
    """Return True when text starts a new item search rather than refining one."""
    if not service._contains_specific_item(text):
        return False
    parsed_kind, _ = parse_report_kind(text)
    return bool(
        parsed_kind == "lost"
        or _is_list_request(text)
        or any(term in text for term in ("幫我找", "協尋", "找一下", "查一下"))
    )


def _is_image_search_request(text: str) -> bool:
    return any(
        term in text
        for term in (
            "類似",
            "相似",
            "像這個",
            "像這張",
            "有看到嗎",
            "有找到嗎",
            "幫我找",
        )
    )


def _is_chat_control(text: str) -> bool:
    normalized = text.strip(" ：:，,。！!")
    return normalized in {
        "一般聊天",
        "繼續聊天",
        "切換為一般聊天",
        "切換一般聊天",
        "退出協尋",
        "取消協尋",
    }


def _is_vague_search_request(text: str) -> bool:
    return _is_list_request(text) and any(
        term in text for term in ("這樣的東西", "這個東西", "這個物品", "類似的東西")
    )


def _is_broad_list_request(text: str) -> bool:
    return any(term in text for term in ("列出", "清單", "有哪些", "有什麼", "撿到什麼"))


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
    location = extract_location(text)
    if location:
        location_matches = [
            item
            for item in reports
            if item.location
            and (
                location.casefold() in item.location.casefold()
                or item.location.casefold() in location.casefold()
            )
        ]
        reports = location_matches
    time_hint = extract_time_hint(text)
    if time_hint:
        center, uncertainty = time_hint
        time_matches = []
        for item in reports:
            item_time = item.occurred_at or item.created_at
            if time_hint_matches(item_time, center, uncertainty):
                time_matches.append(item)
        reports = time_matches
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


async def _temporary_search_messages(
    request: Request,
    service: ReportService,
    text: str,
    image: bytes | None = None,
    line_user_id: str | None = None,
) -> list[dict]:
    combined = text or "使用者上傳的遺失物照片"
    if line_user_id:
        starts_new_search = (
            _get_active_mode(line_user_id) != "lost"
            and _is_complete_item_search(service, text)
        )
        if starts_new_search:
            _pending_intents.pop(line_user_id, None)
            if image is None:
                _pending_images.pop(line_user_id, None)
            previous = None
        else:
            previous = _take_pending_intent(line_user_id)
        clues = [
            clue
            for clue in (
                previous[1] if previous and previous[0] == "lost" else None,
                text or "使用者上傳的遺失物照片",
            )
            if clue
        ]
        combined = "；".join(dict.fromkeys(clues))
        _store_pending_intent(line_user_id, "lost", combined)
        if image:
            _store_pending_image(line_user_id, image)
    results = await service.search_found(
        combined,
        image_bytes=image,
        # A newly supplied location or floor refines the previous search and
        # must take precedence over an older place in the combined context.
        location=extract_location(text) or extract_location(combined),
    )
    if not results:
        return [
            {
                "type": "text",
                "text": "我用目前提供的線索查過了，暫時沒有找到相符的待認領物品。你可以再告訴我顏色、品牌、日期或更精確的地點，我會接著替你比對；也可以開啟持續協尋，之後有相似物品時再通知你。",
                "quickReply": {
                    "items": [
                        {"type": "action", "action": {"type": "postback", "label": "開啟持續協尋", "data": "action=enable_tracking", "displayText": "開啟持續協尋"}},
                        {"type": "action", "action": {"type": "postback", "label": "登記拾獲物", "data": "action=start_found", "displayText": "我撿到東西了"}},
                        {"type": "action", "action": {"type": "postback", "label": "一般聊天", "data": "action=continue_chat", "displayText": "繼續聊天"}},
                    ]
                },
            }
        ]
    lines = [f"我依照你提供的線索，找到 {len(results)} 件較接近的待認領物品："]
    for index, (item, result) in enumerate(results, 1):
        where = item.location or "地點未提供"
        reasons = "、".join(result.reasons) or "外觀或文字特徵接近"
        lines.append(f"{index}. {item.description}｜{where}｜相似度 {result.score:.0%}（{reasons}）")
    best = results[0][0]
    messages: list[dict] = [{"type": "text", "text": "\n".join(lines)[:5000]}]
    if best.images:
        image_url = f"{_public_base_url(request)}/api/v1/reports/{best.id}/image"
        messages.append(
            {
                "type": "image",
                "originalContentUrl": image_url,
                "previewImageUrl": image_url,
            }
        )
    messages.append(
        {
            "type": "text",
            "text": "目前最接近的是上面這件。你可以先看看照片，這有可能是你遺失的物品嗎？",
            "quickReply": {
                "items": [
                    {"type": "action", "action": {"type": "message", "label": "可能是我的", "text": "這可能是我的"}},
                    {"type": "action", "action": {"type": "message", "label": "不是我的", "text": "這不是我的"}},
                    {"type": "action", "action": {"type": "postback", "label": "持續協尋", "data": "action=enable_tracking", "displayText": "開啟持續協尋"}},
                ]
            },
        }
    )
    return messages


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
                "action=enable_tracking",
                "action=confirm_found_now",
                "action=provide_found_time",
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
            if action == "action=enable_tracking":
                pending_intent = _take_pending_intent(line_user_id)
                pending_image = _take_pending_image(line_user_id)
                if not pending_intent:
                    _store_active_mode(line_user_id, "lost")
                    await client.reply(
                        reply_token,
                        "請先描述遺失物的名稱、顏色、地點與時間，或傳一張照片；查詢後才能開啟持續協尋。",
                    )
                    continue
                _, description = pending_intent
                if service._is_generic_item_query(description) and not pending_image:
                    _store_active_mode(line_user_id, "lost")
                    _store_pending_intent(line_user_id, "lost", description)
                    await client.reply(
                        reply_token,
                        "開啟持續協尋前，請再補充物品名稱、顏色或照片，避免建立無法有效比對的案件。",
                    )
                    continue
                report, matches = await service.create(
                    ReportCreate(
                        kind="lost",
                        description=description,
                        location=extract_location(description),
                        line_user_id=line_user_id,
                        image_base64=(
                            base64.b64encode(pending_image).decode("ascii")
                            if pending_image
                            else None
                        ),
                    )
                )
                _clear_active_mode(line_user_id)
                messages = await _candidate_messages(request, session, report, matches)
                messages[0]["text"] = "已開啟持續協尋；只有新的拾獲物與你的線索高度相符時才會通知你。\n" + messages[0]["text"]
                await client.reply(reply_token, messages)
                continue
            if action == "action=provide_found_time":
                _store_active_mode(line_user_id, "found")
                await client.reply(
                    reply_token,
                    "請告訴我撿到時間，例如「今天下午 2 點」、「昨天晚上」或「9/10 上午 10 點」。",
                )
                continue
            if action == "action=confirm_found_now":
                pending_intent = _take_pending_intent(line_user_id)
                pending_image = _take_pending_image(line_user_id)
                if not pending_intent or pending_intent[0] != "found":
                    _store_active_mode(line_user_id, "found")
                    await client.reply(reply_token, "找不到待確認的拾獲資料，請重新描述物品與地點。")
                    continue
                description = pending_intent[1]
                report, matches = await service.create(
                    ReportCreate(
                        kind="found",
                        description=description,
                        location=extract_location(description),
                        occurred_at=datetime.now(
                            timezone(timedelta(hours=8), name="Asia/Taipei")
                        ),
                        line_user_id=line_user_id,
                        image_base64=(
                            base64.b64encode(pending_image).decode("ascii")
                            if pending_image
                            else None
                        ),
                    )
                )
                _clear_active_mode(line_user_id)
                await client.reply(
                    reply_token,
                    await _found_registered_messages(request, session, report, matches),
                )
                continue
            kind = "lost" if action.endswith("lost") else "found"
            _active_modes.pop(line_user_id, None)
            _pending_intents.pop(line_user_id, None)
            _store_active_mode(line_user_id, kind)
            prompt = (
                "請描述遺失物名稱、顏色、地點與時間，或直接傳照片。我會先查詢待認領物品，不會建立案件；查無結果時，你可以自行選擇是否開啟持續協尋通知。"
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
            if _is_chat_control(raw_text):
                if line_user_id:
                    _clear_active_mode(line_user_id)
                    _store_active_mode(line_user_id, "chat")
                await client.reply(
                    reply_token,
                    "已回到一般聊天。你仍可直接詢問遺失物或傳照片搜尋；只有登記拾獲物及明確開啟持續協尋時才會寫入資料庫。",
                )
                continue
            image_follow_up = _has_pending_image(line_user_id) and _is_image_search_request(raw_text)
            if _is_vague_search_request(raw_text) and not image_follow_up:
                await client.reply(
                    reply_token,
                    "可以，請傳一張物品照片，或補充物品名稱、顏色、地點與時間，我才能進行比對。",
                )
                continue
            if (
                _is_list_request(raw_text)
                or _is_search_follow_up(line_user_id, raw_text)
            ) and not image_follow_up:
                _remember_search(line_user_id, raw_text)
                if _is_broad_list_request(raw_text):
                    await client.reply(reply_token, await _list_items_reply(service, raw_text))
                else:
                    await client.reply(
                        reply_token,
                        await _temporary_search_messages(
                            request,
                            service,
                            raw_text,
                            line_user_id=line_user_id,
                        ),
                    )
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
            mode = mode or "chat"
            if mode in {"chat", "lost"}:
                pending_image = (
                    _take_pending_image(line_user_id)
                    if _is_image_search_request(raw_text)
                    else None
                )
                parsed_kind, _ = parse_report_kind(raw_text)
                if parsed_kind == "found":
                    if line_user_id:
                        _store_pending_intent(line_user_id, "found", raw_text)
                    await client.reply(
                        reply_token,
                        _mode_menu_message("看起來你想登記拾獲物。請先點選「我撿到物品」，避免誤將一般聊天寫入資料庫。"),
                    )
                    continue
                if pending_image or parsed_kind == "lost" or mode == "lost":
                    await client.reply(
                        reply_token,
                        await _temporary_search_messages(
                            request,
                            service,
                            raw_text,
                            pending_image,
                            line_user_id,
                        ),
                    )
                    continue
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
            if pending_context and pending_context[0] == kind:
                previous_context = pending_context[1]
            elif pending_context and line_user_id:
                _store_pending_intent(line_user_id, *pending_context)
            pending_image = _take_pending_image(line_user_id)
            combined_description = "；".join(
                dict.fromkeys(
                    value for value in (previous_context, description) if value
                )
            )
            if service._is_generic_item_query(combined_description) and not pending_image:
                if line_user_id:
                    _store_pending_intent(line_user_id, kind, combined_description)
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
            found_time = extract_time_hint(combined_description)
            if kind == "found" and not found_time:
                if line_user_id:
                    _store_pending_intent(line_user_id, kind, combined_description)
                    if pending_image:
                        _store_pending_image(line_user_id, pending_image)
                await client.reply(reply_token, _found_time_confirmation_message())
                continue
            report, matches = await service.create(
                ReportCreate(
                    kind=kind,
                    description=combined_description,
                    location=extract_location(combined_description),
                    occurred_at=found_time[0] if found_time else None,
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
            try:
                image = await client.download_content(message["id"])
            except RuntimeError:
                await client.reply(reply_token, "尚未設定 LINE Access Token。")
                continue
            if mode != "found":
                await client.reply(
                    reply_token,
                    await _temporary_search_messages(
                        request,
                        service,
                        "使用者上傳的遺失物照片",
                        image,
                        line_user_id,
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
            found_time = extract_time_hint(description)
            if kind == "found" and not found_time:
                if line_user_id:
                    _store_pending_intent(line_user_id, kind, description)
                    _store_pending_image(line_user_id, image)
                await client.reply(reply_token, _found_time_confirmation_message())
                continue
            report, matches = await service.create(
                ReportCreate(
                    kind=kind,
                    description=description,
                    location=pending_location or extract_location(description),
                    occurred_at=found_time[0] if found_time else None,
                    image_base64=base64.b64encode(image).decode("ascii"),
                    line_user_id=line_user_id,
                )
            )
            _clear_active_mode(line_user_id)
        else:
            continue

        messages = (
            await _found_registered_messages(request, session, report, matches)
            if report.kind == "found"
            else await _candidate_messages(request, session, report, matches)
        )
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
