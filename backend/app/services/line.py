import base64
import hashlib
import hmac
import re
from typing import Any, Sequence

import httpx

from app.core.config import Settings


class LineClient:
    api_base = "https://api.line.me/v2/bot"
    data_base = "https://api-data.line.me/v2/bot"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def verify_signature(self, body: bytes, signature: str | None) -> bool:
        if self.settings.demo_mode and not self.settings.line_channel_secret:
            return True
        if not signature or not self.settings.line_channel_secret:
            return False
        digest = hmac.new(
            self.settings.line_channel_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
        expected = base64.b64encode(digest).decode("ascii")
        return hmac.compare_digest(expected, signature)

    async def reply(self, reply_token: str, message: str | list[dict]) -> None:
        if not self.settings.line_channel_access_token:
            return
        messages = (
            [{"type": "text", "text": message[:5000]}]
            if isinstance(message, str)
            else message[:5]
        )
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self.api_base}/message/reply",
                headers={
                    "Authorization": f"Bearer {self.settings.line_channel_access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "replyToken": reply_token,
                    "messages": messages,
                },
            )
            response.raise_for_status()

    async def push(self, line_user_id: str, message: str) -> None:
        if not self.settings.line_channel_access_token:
            return
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self.api_base}/message/push",
                headers={
                    "Authorization": f"Bearer {self.settings.line_channel_access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "to": line_user_id,
                    "messages": [{"type": "text", "text": message[:5000]}],
                },
            )
            response.raise_for_status()

    async def download_content(self, message_id: str) -> bytes:
        if not self.settings.line_channel_access_token:
            raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN is required")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.data_base}/message/{message_id}/content",
                headers={
                    "Authorization": f"Bearer {self.settings.line_channel_access_token}"
                },
            )
            response.raise_for_status()
            return response.content


def build_match_reply(
    report_id: str, kind: str, matches: Sequence[Any], has_image: bool = True
) -> str:
    if not matches:
        return (
            "收到，我已經幫你建立協尋案件了。\n"
            "目前還沒有找到相似物品；之後有新的拾獲或遺失通報時，我會繼續比對並通知你。"
        )
    top = matches[0]
    target = "拾獲物" if kind == "lost" else "遺失通報"
    reasons = "、".join(top.reasons) if top.reasons else "多項特徵接近"
    if top.score >= 0.75:
        opening = "有好消息！我找到高度相似的候選。"
    elif top.score >= 0.60:
        opening = "我找到幾個可能相似的候選。"
    else:
        opening = "我找到幾個初步候選，不過目前信心還不高。"
    return (
        f"{opening}\n"
        f"找到 {len(matches)} 個可能相似的{target}。\n"
        f"最高相似度：{top.score:.0%}\n"
        f"判斷依據：{reasons}\n"
        + ("我先把最接近的照片傳給你看看。" if has_image else "這筆候選目前沒有照片，你可以補充更多線索讓我再找一次。")
    )


def build_match_messages(text: str, image_url: str | None) -> list[dict]:
    messages: list[dict] = [{"type": "text", "text": text[:5000]}]
    if image_url:
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
                "text": "這看起來像你的物品嗎？",
                "quickReply": {
                    "items": [
                        {"type": "action", "action": {"type": "message", "label": "可能是我的", "text": "這可能是我的"}},
                        {"type": "action", "action": {"type": "message", "label": "不是我的", "text": "這不是我的"}},
                        {"type": "action", "action": {"type": "message", "label": "再看一次", "text": "再給我看一次照片"}},
                    ]
                },
            }
        )
    return messages


def parse_report_kind(text: str) -> tuple[str | None, str]:
    normalized = text.strip()
    lost_prefixes = ("我有遺失", "我遺失", "遺失", "報失", "不見了", "我掉了")
    found_prefixes = ("我有撿到", "我撿到", "拾獲", "撿到", "找到")
    for prefix in lost_prefixes:
        if normalized.startswith(prefix):
            return "lost", normalized.removeprefix(prefix).strip(" ：:")
    for prefix in found_prefixes:
        if normalized.startswith(prefix):
            return "found", normalized.removeprefix(prefix).strip(" ：:")
    if any(keyword in normalized for keyword in ("不見", "弄丟", "掉了", "找不到")):
        return "lost", normalized
    if any(keyword in normalized for keyword in ("撿到", "拾獲", "有人掉")):
        return "found", normalized
    return None, normalized


def extract_location(text: str) -> str | None:
    """Extract common campus room/building locations from conversational text."""
    room = re.search(r"(?i)([A-Z]{1,4}\s*-?\s*\d{2,4})\s*(教室)?", text)
    if room:
        code = re.sub(r"[\s-]+", "", room.group(1)).upper()
        return f"{code}教室" if room.group(2) else code

    building = re.search(
        r"(圖書館|體育館|活動中心|學生餐廳|餐廳|宿舍|教學大樓|綜合大樓)"
        r"(?:\s*(?:第)?([0-9一二三四五六七八九十]+)\s*(?:樓|F))?",
        text,
        re.IGNORECASE,
    )
    if building:
        floor = f" {building.group(2)}F" if building.group(2) else ""
        return f"{building.group(1)}{floor}"

    floor = re.search(
        r"(?:在|於)\s*((?:第)?[0-9一二三四五六七八九十]+\s*(?:樓|F))",
        text,
        re.IGNORECASE,
    )
    return re.sub(r"\s+", "", floor.group(1)) if floor else None
