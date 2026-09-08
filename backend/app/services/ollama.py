import base64
import json

import httpx

from app.core.config import Settings
from app.schemas.reports import ItemAttributes


class OllamaService:
    """Local Gemma 3 client for conversation and structured item understanding."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def extract_item(
        self, description: str, image_bytes: bytes | None = None
    ) -> ItemAttributes:
        categories = (
            "earphones, phone, wallet, keys, card, umbrella, drink, bottle, "
            "bag, laptop, mouse, glasses, charger, book, clothing, other"
        )
        prompt = (
            "請從使用者文字與照片抽取失物特徵。category 必須使用以下英文值之一："
            f"{categories}。color 使用簡短英文顏色；brand 不確定時為 null；"
            "distinctive_features 使用繁體中文列出形狀、圖案、貼紙、文字、刮痕、"
            "保護殼或掛飾等可見特色；normalized_description 請用自然繁體中文完整描述。"
            "不得捏造看不到或沒有提供的資訊。"
            f"\n使用者文字：{description.strip() or '未提供，請以照片為主'}"
        )
        user_message: dict[str, object] = {"role": "user", "content": prompt}
        if image_bytes:
            user_message["images"] = [base64.b64encode(image_bytes).decode("ascii")]
        content = await self._chat(
            [
                {
                    "role": "system",
                    "content": "你是校園失物招領的結構化資料抽取器，只輸出符合 schema 的 JSON。",
                },
                user_message,
            ],
            response_format=ItemAttributes.model_json_schema(),
            temperature=0,
        )
        attributes = ItemAttributes.model_validate(json.loads(content))
        if not attributes.normalized_description.strip():
            attributes.normalized_description = description.strip()
        return attributes

    async def general_chat(
        self,
        user_text: str,
        history: list[dict[str, str]],
        official_url: str,
    ) -> str:
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "你是 LostLink AI 校園失物招領助理。請使用自然、友善、簡潔的繁體中文，"
                    "每次最多 180 字。現在是一般聊天模式，絕對不要聲稱已建立、修改或刪除案件，"
                    "也不要假裝看過資料庫。不得自行宣稱「目前有人／沒有人撿到」任何物品，"
                    "也不能編造搜尋結果。如果使用者想報失或登記拾獲物，提醒他點選聊天室下方"
                    "的「我遺失物品」或「我撿到物品」。"
                    f"官方手機入口只有這個網址：{official_url}。"
                    "只有使用者詢問網站、入口或連結時才提供，而且必須直接輸出純網址；"
                    "絕對不能編造其他網域，也不要使用 Markdown 連結語法。"
                    "其他日常問候與產品問題可正常回答。"
                ),
            },
            *history[-6:],
            {"role": "user", "content": user_text},
        ]
        return (
            await self._chat(messages, temperature=0.4)
        ).strip()

    async def clarification(
        self,
        kind: str,
        user_text: str,
        previous_context: str | None,
        has_image: bool,
    ) -> str:
        action = "遺失物協尋" if kind == "lost" else "拾獲物登記"
        content = await self._chat(
            [
                {
                    "role": "system",
                    "content": (
                        f"你是 LostLink AI，目前正在收集「{action}」資料。"
                        "請用自然繁體中文簡短確認已收到的資訊，然後只追問最重要的一項缺漏："
                        "物品名稱、顏色/特色、地點或照片。不得聲稱案件已建立。最多 100 字。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"本次訊息：{user_text}\n"
                        f"先前線索：{previous_context or '無'}\n"
                        f"已有照片：{'是' if has_image else '否'}"
                    ),
                },
            ],
            temperature=0.2,
        )
        return content.strip()

    async def _chat(
        self,
        messages: list[dict[str, object]],
        response_format: dict | str | None = None,
        temperature: float = 0.2,
    ) -> str:
        payload: dict[str, object] = {
            "model": self.settings.ollama_model,
            "messages": messages,
            "stream": False,
            "keep_alive": "10m",
            "options": {"temperature": temperature},
        }
        if response_format is not None:
            payload["format"] = response_format
        async with httpx.AsyncClient(timeout=self.settings.ollama_timeout_seconds) as client:
            response = await client.post(
                f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
                json=payload,
            )
            response.raise_for_status()
        content = response.json().get("message", {}).get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Ollama returned an empty response")
        return content
