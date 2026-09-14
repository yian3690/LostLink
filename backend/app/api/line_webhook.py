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
from app.services.ai import (
    COLOR_TERMS,
    COLOR_ZH,
    category_name_zh,
    item_confirmation_candidates,
    needs_item_confirmation,
)
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
_processed_events: dict[str, tuple[float, str]] = {}
_pending_item_confirmations: dict[str, tuple[float, dict[str, object]]] = {}


def _cleanup_pending() -> None:
    cutoff = time.monotonic() - PENDING_TTL_SECONDS
    for storage in (
        _pending_images,
        _pending_intents,
        _active_modes,
        _chat_histories,
        _recent_searches,
        _processed_events,
        _pending_item_confirmations,
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


def _store_item_confirmation(
    line_user_id: str,
    next_mode: str,
    description: str,
    candidates: list[str],
    brand: str | None = None,
    visible_text: list[str] | None = None,
) -> None:
    _cleanup_pending()
    _pending_item_confirmations[line_user_id] = (
        time.monotonic(),
        {
            "next_mode": next_mode,
            "description": description,
            "candidates": candidates[:3],
            "brand": brand,
            "visible_text": (visible_text or [])[:5],
        },
    )


def _take_item_confirmation(line_user_id: str | None) -> dict[str, object] | None:
    _cleanup_pending()
    if not line_user_id:
        return None
    value = _pending_item_confirmations.pop(line_user_id, None)
    return value[1] if value else None


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
    _pending_item_confirmations.pop(line_user_id, None)


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


def _is_duplicate_event(event: dict) -> bool:
    """Return True only for an actual LINE redelivery of webhookEventId."""
    _cleanup_pending()
    event_key = str(event.get("webhookEventId") or "")
    if not event_key:
        return False
    if event_key in _processed_events:
        return True
    _processed_events[event_key] = (time.monotonic(), "processed")
    return False


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


def _lost_photo_intent_message(summary: str) -> list[dict]:
    label = summary.removeprefix("AI 圖像辨識：").strip()[:90] or "這件物品"
    return [
        {
            "type": "text",
            "text": (
                f"照片收到了！我初步辨識為「{label}」。\n"
                "你想用這張照片立即搜尋待認領物品，還是建立持續協尋？"
            ),
            "quickReply": {
                "items": [
                    {"type": "action", "action": {"type": "postback", "label": "立即搜尋", "data": "action=search_photo", "displayText": "用這張照片立即搜尋"}},
                    {"type": "action", "action": {"type": "postback", "label": "建立持續協尋", "data": "action=enable_tracking", "displayText": "用這張照片建立持續協尋"}},
                    {"type": "action", "action": {"type": "postback", "label": "取消", "data": "action=continue_chat", "displayText": "取消照片操作"}},
                ]
            },
        }
    ]


def _photo_role_intent_message(summary: str) -> list[dict]:
    label = summary.removeprefix("AI 圖像辨識：").strip()[:90] or "這件物品"
    return [
        {
            "type": "text",
            "text": (
                f"照片收到了！我初步辨識為「{label}」。\n"
                "請問這是你撿到的物品、你遺失的物品，還是只想詢問其他事情？"
            ),
            "quickReply": {
                "items": [
                    {"type": "action", "action": {"type": "postback", "label": "我撿到的", "data": "action=photo_found", "displayText": "這是我撿到的物品"}},
                    {"type": "action", "action": {"type": "postback", "label": "我遺失的", "data": "action=photo_lost", "displayText": "這是我遺失的物品"}},
                    {"type": "action", "action": {"type": "postback", "label": "其他／取消", "data": "action=continue_chat", "displayText": "其他用途，取消照片操作"}},
                ]
            },
        }
    ]


def _item_name_confirmation_message(
    candidates: list[str],
    brand: str | None = None,
    visible_text: list[str] | None = None,
) -> list[dict]:
    clues = list(dict.fromkeys(value for value in [brand, *(visible_text or [])] if value))
    clue_text = f"我只確認到照片文字／品牌：{'、'.join(clues[:3])}。\n" if clues else ""
    choices = [
        {
            "type": "action",
            "action": {
                "type": "postback",
                "label": name[:20],
                "data": f"action=confirm_item:{index}",
                "displayText": f"這是{name}",
            },
        }
        for index, name in enumerate(candidates[:3])
    ]
    choices.append(
        {
            "type": "action",
            "action": {
                "type": "postback",
                "label": "都不是／自行輸入",
                "data": "action=item_name_other",
                "displayText": "候選都不是，我要自行輸入",
            },
        }
    )
    return [{
        "type": "text",
        "text": (
            "我對照片中的物品名稱還不夠確定，因此尚未建立案件或更新 AI 特徵。\n"
            f"{clue_text}請先選擇正確名稱；若都不對，可以自行輸入。"
        ),
        "quickReply": {"items": choices},
    }]


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


def _is_tracking_request(text: str) -> bool:
    compact = text.replace(" ", "")
    return any(
        term in compact
        for term in (
            "持續搜尋",
            "持續協尋",
            "建立協尋",
            "幫我追蹤",
        )
    ) and not any(term in compact for term in ("關閉", "取消", "停止"))


def _tracking_missing_fields(description: str, has_image: bool) -> list[str]:
    missing: list[str] = []
    if ReportService._is_generic_item_query(description) and not has_image:
        missing.append("物品名稱、顏色或照片")
    if not _latest_tracking_location(description) and not any(
        term in description for term in ("地點不確定", "不知道地點", "地點不知道")
    ):
        missing.append("遺失地點")
    if not extract_time_hint(description) and not any(
        term in description for term in ("時間不確定", "不知道時間", "時間不知道")
    ):
        missing.append("大約遺失時間")
    return missing


def _latest_tracking_location(description: str) -> str | None:
    parts = [value.strip() for value in description.split("；") if value.strip()]
    for part in reversed(parts):
        location = extract_location(part)
        if location:
            return location
    return extract_location(description)


def _latest_tracking_time(description: str):
    parts = [value.strip() for value in description.split("；") if value.strip()]
    for part in reversed(parts):
        time_hint = extract_time_hint(part)
        if time_hint:
            return time_hint
    return extract_time_hint(description)


def _merge_tracking_clue(previous: str, clue: str) -> str:
    """Merge an answer while replacing older location/time-only answers."""
    incoming = clue.strip()
    if not incoming:
        return previous
    new_location = bool(extract_location(incoming)) or any(
        term in incoming for term in ("地點不確定", "不知道地點", "地點不知道")
    )
    new_time = bool(extract_time_hint(incoming)) or any(
        term in incoming for term in ("時間不確定", "不知道時間", "時間不知道")
    )
    kept: list[str] = []
    for part in [value.strip() for value in previous.split("；") if value.strip()]:
        has_item = ReportService._contains_specific_item(part)
        old_location_only = bool(extract_location(part)) and not has_item
        old_time_only = bool(extract_time_hint(part)) and not has_item
        old_unknown_location = any(
            term in part for term in ("地點不確定", "不知道地點", "地點不知道")
        )
        old_unknown_time = any(
            term in part for term in ("時間不確定", "不知道時間", "時間不知道")
        )
        if new_location and (old_location_only or old_unknown_location):
            continue
        if new_time and (old_time_only or old_unknown_time):
            continue
        kept.append(part)
    return "；".join(dict.fromkeys([*kept, incoming]))


def _tracking_details_message(missing: list[str], has_image: bool) -> list[dict]:
    prefix = "照片與初步特徵已收到。" if has_image else "可以，我會幫你建立持續協尋。"
    return [
        {
            "type": "text",
            "text": (
                f"{prefix}請再補充{'、'.join(missing)}；"
                "也可以加上品牌、刮痕、貼紙等文字特徵。"
                "如果真的不確定，可直接說「地點不確定」或「時間不確定」。"
            ),
        }
    ]


def _tracking_confirmation_message(description: str) -> list[dict]:
    time_hint = _latest_tracking_time(description)
    location = _latest_tracking_location(description)
    location_unknown = any(term in description for term in ("地點不確定", "不知道地點", "地點不知道"))
    time_unknown = any(term in description for term in ("時間不確定", "不知道時間", "時間不知道"))
    location_text = location or ("不確定" if location_unknown else "未提供")
    occurred = time_hint[0].strftime("%Y/%m/%d %H:%M") if time_hint else ("不確定" if time_unknown else "未提供")
    item_parts = [
        part for part in description.split("；")
        if ReportService._contains_specific_item(part) or not (extract_location(part) or extract_time_hint(part))
    ]
    item_parts = [
        part for part in item_parts
        if not any(
            term in part
            for term in (
                "地點不確定",
                "不知道地點",
                "地點不知道",
                "時間不確定",
                "不知道時間",
                "時間不知道",
            )
        )
    ]
    summary = "；".join(dict.fromkeys(item_parts))[:220] or description[:220]
    return [
        {
            "type": "text",
            "text": (
                "請確認持續協尋資料：\n"
                f"物品與特徵：{summary}\n"
                f"遺失地點：{location_text}\n"
                f"遺失時間：{occurred}\n"
                "確認後才會建立案件並持續比對新的拾獲物。"
            ),
            "quickReply": {
                "items": [
                    {"type": "action", "action": {"type": "postback", "label": "確認建立", "data": "action=confirm_tracking", "displayText": "確認建立持續協尋"}},
                    {"type": "action", "action": {"type": "message", "label": "補充資料", "text": "我要補充協尋資料"}},
                    {"type": "action", "action": {"type": "postback", "label": "取消", "data": "action=continue_chat", "displayText": "取消建立持續協尋"}},
                ]
            },
        }
    ]


async def _resume_after_item_confirmation(
    request: Request,
    session: AsyncSession,
    service: ReportService,
    line_user_id: str,
    state: dict[str, object],
    image: bytes,
    confirmed_name: str,
) -> list[dict]:
    previous = str(state.get("description") or "").strip()
    confirmed_description = "；".join(
        dict.fromkeys(
            value
            for value in (previous, f"使用者確認物品名稱：{confirmed_name.strip()}")
            if value
        )
    )
    next_mode = str(state.get("next_mode") or "photo")

    if next_mode in {"tracking", "tracking_confirm"}:
        _store_pending_intent(line_user_id, "lost", confirmed_description)
        _store_pending_image(line_user_id, image)
        missing = _tracking_missing_fields(confirmed_description, True)
        if missing:
            _store_active_mode(line_user_id, "tracking")
            return _tracking_details_message(missing, True)
        _store_active_mode(line_user_id, "tracking_confirm")
        return _tracking_confirmation_message(confirmed_description)

    if next_mode == "lost":
        _store_pending_intent(line_user_id, "lost", confirmed_description)
        _store_pending_image(line_user_id, image)
        _store_active_mode(line_user_id, "lost")
        return _lost_photo_intent_message(confirmed_description)

    if next_mode == "found":
        location = extract_location(confirmed_description)
        found_time = extract_time_hint(confirmed_description)
        if not location:
            _store_pending_intent(line_user_id, "found", confirmed_description)
            _store_pending_image(line_user_id, image)
            _store_active_mode(line_user_id, "found")
            return [{"type": "text", "text": f"已確認這是「{confirmed_name}」。請再告訴我撿到地點與時間，確認完整後才會建立案件。"}]
        if not found_time:
            _store_pending_intent(line_user_id, "found", confirmed_description)
            _store_pending_image(line_user_id, image)
            _store_active_mode(line_user_id, "found")
            return _found_time_confirmation_message()
        report, matches = await service.create(
            ReportCreate(
                kind="found",
                description=confirmed_description,
                location=location,
                occurred_at=found_time[0],
                line_user_id=line_user_id,
                image_base64=base64.b64encode(image).decode("ascii"),
            )
        )
        _clear_active_mode(line_user_id)
        return await _found_registered_messages(request, session, report, matches)

    _store_pending_intent(line_user_id, "photo", confirmed_description)
    _store_pending_image(line_user_id, image)
    _store_active_mode(line_user_id, "chat")
    return _photo_role_intent_message(confirmed_description)


async def _found_registered_messages(
    request: Request,
    session: AsyncSession,
    report: ItemReport,
    matches,
) -> list[dict]:
    if matches:
        return [{
            "type": "text",
            "text": (
                "感謝提供資訊，我已經收到你提供的資料，拾獲物已完成登記。\n"
                "系統找到可能相符的遺失案件，會通知最相符的失主進一步確認。"
                "為保護雙方隱私，不會在這裡顯示失主的登記內容，也不會把你剛上傳的照片當成候選物品傳回。"
            ),
        }]
    return [{
        "type": "text",
        "text": (
            "感謝提供資訊，我已經收到你提供的資料，拾獲物已完成登記。\n"
            "目前還沒有相符的遺失案件；之後若出現高度相符的持續協尋，系統會再通知失主確認。"
        ),
    }]


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
    if not line_user_id:
        return False
    normalized = text.strip()
    if len(normalized) > 40:
        return False
    if line_user_id in _recent_searches and normalized.endswith(("呢", "嗎", "？", "?")):
        return True
    pending = _pending_intents.get(line_user_id)
    has_pending_lost_search = bool(pending and pending[1] == "lost")
    if not has_pending_lost_search:
        return False
    has_color = any(
        term.casefold() in normalized.casefold()
        for terms in COLOR_TERMS.values()
        for term in terms
    )
    return bool(
        has_color
        or ReportService._contains_specific_item(normalized)
        or extract_location(normalized)
        or extract_time_hint(normalized)
    )


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
        if _is_duplicate_event(event):
            continue
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
                "action=confirm_tracking",
                "action=search_photo",
                "action=photo_found",
                "action=photo_lost",
                "action=confirm_found_now",
                "action=provide_found_time",
                "action=item_name_other",
            } and not action.startswith("action=confirm_item:"):
                await client.reply(reply_token, _mode_menu_message())
                continue
            if not line_user_id:
                await client.reply(reply_token, "無法取得 LINE 使用者資料，請重新開啟聊天室後再試。")
                continue
            if action == "action=item_name_other":
                state = _take_item_confirmation(line_user_id)
                image = _take_pending_image(line_user_id)
                if not state or not image:
                    await client.reply(reply_token, "找不到剛才的照片辨識結果，請重新傳送照片。")
                    continue
                _store_item_confirmation(
                    line_user_id,
                    str(state.get("next_mode") or "photo"),
                    str(state.get("description") or ""),
                    [str(value) for value in state.get("candidates", [])],
                    str(state.get("brand") or "") or None,
                    [str(value) for value in state.get("visible_text", [])],
                )
                _store_pending_image(line_user_id, image)
                _store_active_mode(line_user_id, "item_name_input")
                await client.reply(reply_token, "請直接輸入這件物品的完整名稱，例如「按摩滾筒」或「CeraVe 乳液」。收到後我會再讓你確認案件資料。")
                continue
            if action.startswith("action=confirm_item:"):
                state = _take_item_confirmation(line_user_id)
                image = _take_pending_image(line_user_id)
                try:
                    choice_index = int(action.rsplit(":", 1)[1])
                    candidates = [str(value) for value in (state or {}).get("candidates", [])]
                    confirmed_name = candidates[choice_index]
                except (ValueError, IndexError):
                    state = None
                    confirmed_name = ""
                if not state or not image or not confirmed_name:
                    await client.reply(reply_token, "找不到剛才的候選項目，請重新傳送照片。")
                    continue
                await client.reply(
                    reply_token,
                    await _resume_after_item_confirmation(
                        request, session, service, line_user_id, state, image, confirmed_name
                    ),
                )
                continue
            if action == "action=continue_chat":
                _clear_active_mode(line_user_id)
                _store_active_mode(line_user_id, "chat")
                await client.reply(
                    reply_token,
                    "已進入一般聊天。接下來的文字與照片都不會建立案件；需要協尋或登記時，再從下方選單選擇即可。",
                )
                continue
            if action in {"action=photo_found", "action=photo_lost"}:
                pending_intent = _take_pending_intent(line_user_id)
                pending_image = _take_pending_image(line_user_id)
                if not pending_intent or pending_intent[0] != "photo" or not pending_image:
                    await client.reply(reply_token, "找不到剛才的照片，請重新傳送一次。")
                    continue
                description = pending_intent[1]
                kind = "found" if action == "action=photo_found" else "lost"
                _store_pending_intent(line_user_id, kind, description)
                _store_pending_image(line_user_id, pending_image)
                _store_active_mode(line_user_id, kind)
                if kind == "found":
                    await client.reply(
                        reply_token,
                        "了解，這是你撿到的物品。請再告訴我撿到地點與時間；也可以補充品牌、文字、刮痕或貼紙等特徵。資料確認完整後才會建立拾獲案件。",
                    )
                else:
                    await client.reply(reply_token, _lost_photo_intent_message(description))
                continue
            if action == "action=search_photo":
                pending_image = _take_pending_image(line_user_id)
                if not pending_image:
                    await client.reply(
                        reply_token,
                        "找不到剛才的照片，請重新傳送一次。",
                    )
                    continue
                await client.reply(
                    reply_token,
                    await _temporary_search_messages(
                        request,
                        service,
                        "",
                        pending_image,
                        line_user_id,
                    ),
                )
                continue
            if action == "action=enable_tracking":
                pending_intent = _take_pending_intent(line_user_id)
                pending_image = _take_pending_image(line_user_id)
                if not pending_intent:
                    _store_active_mode(line_user_id, "tracking")
                    await client.reply(
                        reply_token,
                        _tracking_details_message(
                            ["物品名稱、顏色或照片", "遺失地點", "大約遺失時間"],
                            False,
                        ),
                    )
                    continue
                _, description = pending_intent
                _store_active_mode(line_user_id, "tracking")
                _store_pending_intent(line_user_id, "lost", description)
                if pending_image:
                    _store_pending_image(line_user_id, pending_image)
                missing = _tracking_missing_fields(description, bool(pending_image))
                if missing:
                    await client.reply(
                        reply_token,
                        _tracking_details_message(missing, bool(pending_image)),
                    )
                    continue
                _store_active_mode(line_user_id, "tracking_confirm")
                await client.reply(reply_token, _tracking_confirmation_message(description))
                continue
            if action == "action=confirm_tracking":
                pending_intent = _take_pending_intent(line_user_id)
                pending_image = _take_pending_image(line_user_id)
                if not pending_intent or pending_intent[0] != "lost":
                    _store_active_mode(line_user_id, "tracking")
                    await client.reply(
                        reply_token,
                        _tracking_details_message(
                            ["物品名稱、顏色或照片", "遺失地點", "大約遺失時間"],
                            False,
                        ),
                    )
                    continue
                description = pending_intent[1]
                missing = _tracking_missing_fields(description, bool(pending_image))
                if missing:
                    _store_pending_intent(line_user_id, "lost", description)
                    if pending_image:
                        _store_pending_image(line_user_id, pending_image)
                    _store_active_mode(line_user_id, "tracking")
                    await client.reply(reply_token, _tracking_details_message(missing, bool(pending_image)))
                    continue
                time_hint = _latest_tracking_time(description)
                report, matches = await service.create(
                    ReportCreate(
                        kind="lost",
                        description=description,
                        location=_latest_tracking_location(description),
                        occurred_at=time_hint[0] if time_hint else None,
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
                "請描述遺失物名稱、顏色、地點與時間，或直接傳照片。收到照片後我會先詢問用途；只有你確認時才會搜尋或建立持續協尋。"
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
            if _get_active_mode(line_user_id) == "item_name_input":
                state = _take_item_confirmation(line_user_id)
                image = _take_pending_image(line_user_id)
                if not state or not image:
                    _clear_active_mode(line_user_id)
                    await client.reply(reply_token, "剛才的照片辨識已逾時，請重新傳送照片。")
                    continue
                if _generic_description(raw_text) or len(raw_text) > 40:
                    _store_item_confirmation(
                        line_user_id,
                        str(state.get("next_mode") or "photo"),
                        str(state.get("description") or ""),
                        [str(value) for value in state.get("candidates", [])],
                        str(state.get("brand") or "") or None,
                        [str(value) for value in state.get("visible_text", [])],
                    )
                    _store_pending_image(line_user_id, image)
                    await client.reply(reply_token, "請輸入 2～40 字的具體物品名稱，例如「黑色按摩滾筒」，不要只寫「東西」。")
                    continue
                await client.reply(
                    reply_token,
                    await _resume_after_item_confirmation(
                        request, session, service, line_user_id, state, image, raw_text
                    ),
                )
                continue
            if _is_chat_control(raw_text):
                if line_user_id:
                    _clear_active_mode(line_user_id)
                    _store_active_mode(line_user_id, "chat")
                await client.reply(
                    reply_token,
                    "已回到一般聊天。你仍可直接詢問遺失物或傳照片搜尋；只有登記拾獲物及明確開啟持續協尋時才會寫入資料庫。",
                )
                continue
            if _is_tracking_request(raw_text):
                if line_user_id:
                    _clear_active_mode(line_user_id)
                    _store_active_mode(line_user_id, "tracking")
                await client.reply(
                    reply_token,
                    _tracking_details_message(
                        ["物品名稱、顏色或照片", "遺失地點", "大約遺失時間"],
                        False,
                    ),
                )
                continue
            active_mode = _get_active_mode(line_user_id)
            if active_mode in {"tracking", "tracking_confirm"}:
                pending_context = _take_pending_intent(line_user_id)
                previous = (
                    pending_context[1]
                    if pending_context and pending_context[0] == "lost"
                    else ""
                )
                pending_image = _take_pending_image(line_user_id)
                combined = _merge_tracking_clue(previous, raw_text)
                _store_pending_intent(line_user_id, "lost", combined)
                if pending_image:
                    _store_pending_image(line_user_id, pending_image)
                missing = _tracking_missing_fields(combined, bool(pending_image))
                if missing:
                    _store_active_mode(line_user_id, "tracking")
                    await client.reply(
                        reply_token,
                        _tracking_details_message(missing, bool(pending_image)),
                    )
                else:
                    _store_active_mode(line_user_id, "tracking_confirm")
                    await client.reply(
                        reply_token,
                        _tracking_confirmation_message(combined),
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
                if not line_user_id:
                    await client.reply(reply_token, "無法取得 LINE 使用者資料，請重新開啟聊天室後再試。")
                    continue
                _cleanup_pending()
                pending_value = _pending_intents.get(line_user_id)
                pending_description = (
                    pending_value[2]
                    if pending_value and pending_value[1] == "lost"
                    else ""
                )
                if pending_description and _is_image_search_request(pending_description):
                    await client.reply(
                        reply_token,
                        await _temporary_search_messages(
                            request,
                            service,
                            pending_description,
                            image,
                            line_user_id,
                        ),
                    )
                    continue
                attributes = await service.analyzer.analyze(
                    pending_description or "使用者上傳的遺失物照片",
                    image,
                )
                analyzed_description = (
                    attributes.normalized_description
                    or pending_description
                    or "使用者上傳的遺失物照片"
                )
                if _generic_description(analyzed_description):
                    visual_summary = "".join(
                        (
                            COLOR_ZH.get(attributes.color or "", ""),
                            category_name_zh(attributes.category),
                        )
                    )
                    analyzed_description = f"AI 圖像辨識：{visual_summary}"
                if pending_description and not _generic_description(pending_description):
                    analyzed_description = "；".join(
                        dict.fromkeys((pending_description, analyzed_description))
                    )
                if needs_item_confirmation(
                    attributes,
                    pending_description,
                    True,
                    settings.item_confirmation_threshold,
                ):
                    candidates = item_confirmation_candidates(attributes)
                    _store_item_confirmation(
                        line_user_id,
                        mode or "photo",
                        analyzed_description,
                        candidates,
                        attributes.brand,
                        attributes.visible_text,
                    )
                    _store_pending_image(line_user_id, image)
                    _store_active_mode(line_user_id, "item_confirm")
                    await client.reply(
                        reply_token,
                        _item_name_confirmation_message(
                            candidates, attributes.brand, attributes.visible_text
                        ),
                    )
                    continue
                if mode in {"tracking", "tracking_confirm"}:
                    _store_pending_intent(line_user_id, "lost", analyzed_description)
                    _store_pending_image(line_user_id, image)
                    missing = _tracking_missing_fields(analyzed_description, True)
                    if missing:
                        _store_active_mode(line_user_id, "tracking")
                        await client.reply(
                            reply_token,
                            _tracking_details_message(missing, True),
                        )
                    else:
                        _store_active_mode(line_user_id, "tracking_confirm")
                        await client.reply(
                            reply_token,
                            _tracking_confirmation_message(analyzed_description),
                        )
                    continue
                if mode == "lost":
                    _store_pending_intent(line_user_id, "lost", analyzed_description)
                    _store_pending_image(line_user_id, image)
                    await client.reply(
                        reply_token,
                        _lost_photo_intent_message(analyzed_description),
                    )
                    continue
                _store_pending_intent(line_user_id, "photo", analyzed_description)
                _store_pending_image(line_user_id, image)
                await client.reply(
                    reply_token,
                    _photo_role_intent_message(analyzed_description),
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
            attributes = await service.analyzer.analyze(description, image)
            if needs_item_confirmation(
                attributes,
                description,
                True,
                settings.item_confirmation_threshold,
            ):
                analyzed_description = attributes.normalized_description or description
                candidates = item_confirmation_candidates(attributes)
                _store_item_confirmation(
                    line_user_id,
                    kind,
                    analyzed_description,
                    candidates,
                    attributes.brand,
                    attributes.visible_text,
                )
                _store_pending_image(line_user_id, image)
                _store_active_mode(line_user_id, "item_confirm")
                await client.reply(
                    reply_token,
                    _item_name_confirmation_message(
                        candidates, attributes.brand, attributes.visible_text
                    ),
                )
                continue
            if _generic_description(description):
                description = attributes.normalized_description or (
                    "拾獲者上傳的物品照片" if kind == "found" else "使用者上傳的遺失物照片"
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
