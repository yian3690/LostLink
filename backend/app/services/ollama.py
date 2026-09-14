import base64
import json

import httpx

from app.core.config import Settings
from app.schemas.reports import ItemAttributes, ItemClassification


class OllamaService:
    """Local Gemma 3 client for conversation and structured item understanding."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def extract_item(
        self,
        description: str,
        image_bytes: bytes | None = None,
        correction_context: list[str] | None = None,
    ) -> ItemAttributes:
        classification = await self.classify_item(
            description, image_bytes, correction_context
        )
        return await self.extract_item_details(
            description, classification, image_bytes, correction_context
        )

    async def classify_item(
        self,
        description: str,
        image_bytes: bytes | None = None,
        correction_context: list[str] | None = None,
    ) -> ItemClassification:
        categories = (
            "earphones, phone, wallet, keys, card, umbrella, drink, bottle, "
            "bag, laptop, mouse, glasses, charger, book, clothing, foam_roller, "
            "toiletries, stationery, watch, jewelry, shoes, helmet, ball, toy, personal_item"
        )
        prompt = (
            "這是第一階段，只判斷照片中使用者要登記的主要物品類別，不要描述外觀。"
            "category 必須使用以下英文值之一："
            f"{categories}。若物品不屬於清單，category 必須直接填 2 到 12 字的繁體中文物品名稱，"
            "例如計算機、樂器盒或運動器材；只有真的無法辨識物品名稱時才可使用 personal_item，禁止輸出 other。"
            "recognition_confidence 填 0 到 1；背景複雜、有多個主要物品、被遮擋或模糊時必須低於 0.68。"
            "item_name_candidates 依可能性列出最多 3 個完整繁體中文物品名稱。"
            "優先採用使用者明確說出的物品名稱，但不得把地點或背景物品當成主要物品。"
            f"\n使用者文字：{description.strip() or '未提供，請以照片為主'}"
            f"\n先前檢查問題：{'；'.join(correction_context or []) or '無'}"
        )
        classification = ItemClassification.model_validate(
            json.loads(
                await self._structured_chat(
                    prompt,
                    image_bytes,
                    ItemClassification.model_json_schema(),
                    "你是校園失物招領的物品類別判斷器，只輸出符合 schema 的 JSON。",
                )
            )
        )
        return classification

    async def extract_item_details(
        self,
        description: str,
        classification: ItemClassification,
        image_bytes: bytes | None = None,
        correction_context: list[str] | None = None,
    ) -> ItemAttributes:
        prompt = (
            "這是第二階段。主要物品類別已由第一階段決定，請只針對該類別抽取可核對的外觀。"
            f"category 必須固定為：{classification.category or 'personal_item'}，不可改成背景物品。"
            "color 使用主要物品的簡短英文主色；brand 不確定時為 null；"
            "distinctive_features 使用繁體中文列出 3 到 6 項彼此不同、可由照片核對的具體特色，"
            "優先描述形狀、圖案、貼紙、可見文字、刮痕、材質、表面紋理、大小、開合方式、"
            "邊角、保護殼、掛飾、配件及其所在位置。每項應說明具體外觀，例如「充電盒為圓角矩形」"
            "或「盒蓋正面有白色刮痕」，不要只填單獨的顏色、品牌或物品類別，"
            "也不要用「有文字」「有標誌」等無法區分物品的籠統描述；看不清楚時寧可少列，禁止猜測。"
            "feature_confidences 必須用 distinctive_features 的完整文字作為鍵，逐項填入 0 到 1；"
            "清楚可見可填 0.8 以上，需推測或看不清楚必須低於 0.68。"
            "只辨識主要物品本身，忽略桌面、櫃子、包裝罐及其他背景物品上的文字與特徵。"
            "照片中沒有尺、容量標示或使用者明確說明時，禁止猜測公分、毫升等精確尺寸或容量。"
            "normalized_description 請用自然繁體中文完整描述物品、顏色、材質與主要特色。"
            f"recognition_confidence 固定採用第一階段分數 {classification.recognition_confidence if classification.recognition_confidence is not None else 0.5}。"
            "item_name_candidates 請依可能性列出最多 3 個簡短、完整的繁體中文物品名稱，不要輸出 other。"
            "visible_text 只能逐項填入照片中確實看得到的品牌、型號或文字；看不清楚就留空，禁止猜測。"
            "visible_text 只能來自主要物品表面，背景物品上的文字必須忽略。"
            "不得捏造看不到或沒有提供的資訊。"
            f"\n使用者文字：{description.strip() or '未提供，請以照片為主'}"
            f"\n第一階段候選：{'、'.join(classification.item_name_candidates) or '無'}"
            f"\n先前檢查問題：{'；'.join(correction_context or []) or '無'}"
        )
        content = await self._structured_chat(
            prompt,
            image_bytes,
            ItemAttributes.model_json_schema(),
            "你是校園失物招領的類別專屬特徵抽取器，只輸出符合 schema 的 JSON。",
        )
        attributes = ItemAttributes.model_validate(json.loads(content))
        attributes.category = classification.category
        attributes.recognition_confidence = classification.recognition_confidence
        if not attributes.item_name_candidates:
            attributes.item_name_candidates = classification.item_name_candidates
        if not attributes.normalized_description.strip():
            attributes.normalized_description = description.strip()
        return attributes

    async def _structured_chat(
        self,
        prompt: str,
        image_bytes: bytes | None,
        response_format: dict,
        system_prompt: str,
    ) -> str:
        user_message: dict[str, object] = {"role": "user", "content": prompt}
        if image_bytes:
            user_message["images"] = [base64.b64encode(image_bytes).decode("ascii")]
        return await self._chat(
            [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                user_message,
            ],
            response_format=response_format,
            temperature=0,
        )

    async def refine_item_candidates(
        self,
        brand: str | None,
        visible_text: list[str],
        current_candidates: list[str],
        web_evidence: list[str],
    ) -> list[str]:
        content = await self._chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是物品名稱校對器。只能依品牌、照片可見文字、既有候選與 Wikimedia 文字摘要，"
                        "列出最多 3 個適合失物招領的繁體中文完整物品名稱。品牌維持原文；"
                        "不要猜型號、不要輸出 other，也不要加入解釋。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"品牌：{brand or '無'}\n"
                        f"照片可見文字：{'、'.join(visible_text) or '無'}\n"
                        f"既有候選：{'、'.join(current_candidates) or '無'}\n"
                        f"Wikimedia 摘要：{' | '.join(web_evidence) or '無'}"
                    ),
                },
            ],
            response_format={
                "type": "object",
                "properties": {
                    "candidates": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 3,
                    }
                },
                "required": ["candidates"],
            },
            temperature=0,
        )
        values = json.loads(content).get("candidates", [])
        return [str(value).strip() for value in values if str(value).strip()][:3]

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
                    "口吻像耐心的校園服務台人員，不要使用制式客服開場，也不要重複使用者整句話。"
                    "先直接回答問題，需要時再用一句話引導下一步；每次最多 180 字。"
                    "現在是一般聊天模式，絕對不要聲稱已建立、修改或刪除案件，"
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
                        "請像校園服務台人員一樣，用自然繁體中文簡短確認已收到的資訊；"
                        "不要使用『您好』『很高興為您服務』等制式開場，也不要逐字重複使用者原句。"
                        "接著只追問最重要的一項缺漏："
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
