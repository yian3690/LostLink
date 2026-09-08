import base64
import hashlib
import io
import json
import math
import re
from functools import lru_cache

import numpy as np

from app.core.config import Settings
from app.schemas.reports import ItemAttributes


CATEGORY_TERMS = {
    "earphones": ("airpods", "耳機", "耳塞", "藍牙耳機", "無線耳機"),
    "phone": ("手機", "iphone", "android", "電話"),
    "wallet": ("錢包", "皮夾", "卡夾"),
    "keys": ("鑰匙", "key"),
    "card": ("學生證", "悠遊卡", "信用卡", "證件"),
    "umbrella": ("雨傘", "傘"),
    "drink": ("飲料", "瓶裝飲料", "寶特瓶", "礦泉水", "茶飲", "咖啡"),
    "bottle": ("水壺", "保溫瓶", "水瓶"),
    "bag": ("背包", "書包", "提袋", "袋子"),
    "laptop": ("筆電", "電腦", "macbook", "notebook"),
}
COLOR_TERMS = {
    "black": ("黑色", "黑", "black"),
    "white": ("白色", "白", "white"),
    "blue": ("藍色", "藍", "blue"),
    "red": ("紅色", "紅", "red"),
    "green": ("綠色", "綠", "green"),
    "gray": ("灰色", "灰", "銀色", "gray", "silver"),
    "pink": ("粉紅", "粉色", "pink"),
}
BRANDS = ("apple", "airpods", "sony", "samsung", "xiaomi", "小米", "華碩", "asus")


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


class MultimodalAnalyzer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def analyze(
        self, description: str, image_bytes: bytes | None = None
    ) -> ItemAttributes:
        if self.settings.demo_mode or not self.settings.gemini_api_key:
            return self._rule_based(description, bool(image_bytes))
        try:
            return await self._gemini(description, image_bytes)
        except Exception:
            # Keep registration available if the external model is temporarily down.
            return self._rule_based(description, bool(image_bytes))

    def _rule_based(self, description: str, has_image: bool) -> ItemAttributes:
        text = description.strip()
        category = next(
            (name for name, terms in CATEGORY_TERMS.items() if _contains(text, terms)),
            None,
        )
        color = next(
            (name for name, terms in COLOR_TERMS.items() if _contains(text, terms)),
            None,
        )
        lowered = text.lower()
        brand = next((brand for brand in BRANDS if brand in lowered), None)
        if brand == "airpods":
            brand = "apple"
        features: list[str] = []
        for pattern in ("pro", "刻字", "刮痕", "保護殼", "吊飾", "貼紙"):
            if pattern in lowered:
                features.append(pattern)
        normalized = text
        if has_image and not normalized:
            normalized = "使用者上傳的拾獲物品照片"
        return ItemAttributes(
            category=category,
            brand=brand,
            color=color,
            distinctive_features=features,
            normalized_description=normalized,
        )

    async def _gemini(
        self, description: str, image_bytes: bytes | None
    ) -> ItemAttributes:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.settings.gemini_api_key)
        prompt = (
            "你是校園失物招領資料抽取器。依據文字與照片輸出 JSON，"
            "欄位為 category、brand、color、distinctive_features、"
            "normalized_description。不要猜測照片看不到的資訊。"
            f"\n使用者描述：{description or '未提供'}"
        )
        contents: list[object] = [prompt]
        if image_bytes:
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))
        response = await client.aio.models.generate_content(
            model=self.settings.gemini_model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=ItemAttributes.model_json_schema(),
                temperature=0,
            ),
        )
        return ItemAttributes.model_validate(json.loads(response.text))


class EmbeddingService:
    dimension = 768

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._e5 = None
        self._siglip_model = None
        self._siglip_processor = None
        self._device = None

    def _get_device(self) -> str:
        if self._device is None:
            import torch

            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        return self._device

    async def encode_e5(self, text: str) -> list[float]:
        if self.settings.demo_mode:
            return self._hash_embedding(self._canonicalize(text), "e5")
        if self._e5 is None:
            from sentence_transformers import SentenceTransformer

            self._e5 = SentenceTransformer(
                self.settings.e5_model, device=self._get_device()
            )
        vector = self._e5.encode(
            [f"query: {text}"], normalize_embeddings=True
        )[0]
        return vector.astype(float).tolist()

    async def encode_siglip_text(self, text: str) -> list[float]:
        if self.settings.demo_mode:
            return self._hash_embedding(self._canonicalize(text), "siglip")
        self._load_siglip()
        import torch

        inputs = self._siglip_processor(
            text=[text], padding="max_length", truncation=True, return_tensors="pt"
        )
        inputs = {name: value.to(self._get_device()) for name, value in inputs.items()}
        with torch.inference_mode():
            output = self._siglip_model.get_text_features(**inputs)
            vector = self._pooled_feature(output)
        vector = vector / vector.norm(p=2)
        return vector.cpu().float().tolist()

    async def encode_siglip_image(self, image_bytes: bytes) -> list[float]:
        if self.settings.demo_mode:
            digest = hashlib.sha256(image_bytes).hexdigest()
            return self._hash_embedding(digest, "siglip-image")
        self._load_siglip()
        import torch
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        inputs = self._siglip_processor(images=[image], return_tensors="pt")
        inputs = {name: value.to(self._get_device()) for name, value in inputs.items()}
        with torch.inference_mode():
            output = self._siglip_model.get_image_features(**inputs)
            vector = self._pooled_feature(output)
        vector = vector / vector.norm(p=2)
        return vector.cpu().float().tolist()

    def _load_siglip(self) -> None:
        if self._siglip_model is None:
            from transformers import AutoModel, AutoProcessor

            self._siglip_processor = AutoProcessor.from_pretrained(
                self.settings.siglip_model
            )
            self._siglip_model = AutoModel.from_pretrained(
                self.settings.siglip_model
            )
            self._siglip_model.to(self._get_device())
            self._siglip_model.eval()

    @staticmethod
    def _pooled_feature(output):
        pooled = getattr(output, "pooler_output", None)
        if pooled is not None:
            return pooled[0]
        return output[0]

    @staticmethod
    def _canonicalize(text: str) -> str:
        lowered = text.lower()
        replacements = {
            "airpods pro": "apple 黑色 耳機 pro",
            "airpods": "apple 耳機",
            "無線藍牙耳機": "耳機",
            "無線耳機": "耳機",
            "藍牙耳機": "耳機",
            "圖書館三樓": "圖書館 3f",
            "圖書館二樓": "圖書館 2f",
        }
        for source, target in replacements.items():
            lowered = lowered.replace(source, target)
        return lowered

    @classmethod
    def _hash_embedding(cls, text: str, namespace: str) -> list[float]:
        vector = np.zeros(cls.dimension, dtype=np.float32)
        normalized = re.sub(r"\s+", " ", text.strip().lower())
        tokens = list(normalized) + normalized.split()
        tokens += [normalized[index : index + 2] for index in range(len(normalized) - 1)]
        for token in tokens:
            digest = hashlib.blake2b(
                f"{namespace}:{token}".encode("utf-8"), digest_size=8
            ).digest()
            index = int.from_bytes(digest[:4], "little") % cls.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = float(np.linalg.norm(vector))
        if math.isclose(norm, 0.0):
            vector[0] = 1.0
            norm = 1.0
        return (vector / norm).astype(float).tolist()


@lru_cache(maxsize=4)
def _cached_embedding_service(
    demo_mode: bool, e5_model: str, siglip_model: str
) -> EmbeddingService:
    settings = Settings(
        demo_mode=demo_mode,
        e5_model=e5_model,
        siglip_model=siglip_model,
    )
    return EmbeddingService(settings)


def get_embedding_service(settings: Settings) -> EmbeddingService:
    return _cached_embedding_service(
        settings.demo_mode, settings.e5_model, settings.siglip_model
    )
