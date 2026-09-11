import hashlib
import io
import json
import math
import re
from functools import lru_cache

import httpx
import numpy as np

from app.core.config import Settings
from app.schemas.reports import ItemAttributes
from app.services.ollama import OllamaService


CATEGORY_TERMS = {
    "earphones": ("airpods", "耳機", "耳塞", "藍牙耳機", "無線耳機"),
    "phone": ("手機", "iphone", "android", "電話"),
    "wallet": ("錢包", "皮夾", "卡夾"),
    "keys": ("鑰匙", "key"),
    "card": ("學生證", "悠遊卡", "信用卡", "證件"),
    "umbrella": ("雨傘", "傘"),
    "drink": ("飲料", "瓶裝飲料", "寶特瓶", "礦泉水", "茶飲", "咖啡"),
    "bottle": ("水壺", "水杯", "保溫杯", "保溫瓶", "隨行杯", "水瓶", "瓶子"),
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
    "yellow": ("黃色", "黃", "yellow"),
    "brown": ("棕色", "咖啡色", "褐色", "brown"),
    "orange": ("橘色", "橙色", "orange"),
    "purple": ("紫色", "紫", "purple"),
    "transparent": ("透明", "clear", "transparent"),
}
BRANDS = ("apple", "airpods", "sony", "samsung", "xiaomi", "小米", "華碩", "asus")

IMAGE_CATEGORY_PROMPTS = {
    "earphones": "a clear photo of earbuds, earphones, or headphones",
    "phone": "a clear photo of a mobile phone or smartphone",
    "wallet": "a clear photo of a wallet, purse, or card holder",
    "keys": "a clear photo of keys or a key ring",
    "card": "a clear photo of an ID card, student card, or transit card",
    "umbrella": "a clear photo of an umbrella",
    "drink": "a clear photo of a disposable bottled drink or beverage",
    "bottle": "a clear photo of a reusable water bottle or thermos",
    "bag": "a clear photo of a backpack, handbag, or tote bag",
    "laptop": "a clear photo of a laptop computer",
    "mouse": "a clear photo of a computer mouse",
    "glasses": "a clear photo of eyeglasses or sunglasses",
    "charger": "a clear photo of a charger, cable, or power adapter",
    "book": "a clear photo of a book or notebook",
    "clothing": "a clear photo of clothing, a hat, or a scarf",
    "other": "a clear photo of another personal belonging",
}
IMAGE_COLOR_PROMPTS = {
    "black": "a mostly black object",
    "white": "a mostly white object",
    "blue": "a mostly blue object",
    "red": "a mostly red object",
    "green": "a mostly green object",
    "gray": "a mostly gray or silver object",
    "pink": "a mostly pink object",
    "yellow": "a mostly yellow object",
    "brown": "a mostly brown object",
    "orange": "a mostly orange object",
    "purple": "a mostly purple object",
    "transparent": "a mostly transparent clear object",
    "multicolor": "a multicolored object",
}
IMAGE_SHAPE_PROMPTS = {
    "rectangular": "a rectangular or box-shaped object",
    "round": "a round, circular, or oval object",
    "cylindrical": "a tall cylindrical object",
    "long": "a long and narrow object",
    "folded": "a folded compact object",
    "irregular": "an irregularly shaped object",
}
IMAGE_FEATURE_PROMPTS = {
    "plain": "a plain object without a visible pattern",
    "patterned": "an object with a visible decorative pattern",
    "text_or_logo": "an object with visible text or a logo",
    "sticker": "an object with a visible sticker",
    "case": "an object with a protective case or cover",
    "strap": "an object with a strap, cord, or keychain",
    "scratched": "an object with visible scratches or wear",
}
IMAGE_MATERIAL_PROMPTS = {
    "leather": "an object mainly made of leather or synthetic leather",
    "metal": "an object mainly made of metal",
    "plastic": "an object mainly made of plastic",
    "fabric": "an object mainly made of fabric or canvas",
    "rubber": "an object mainly made of rubber or silicone",
    "glass": "an object mainly made of glass",
}
CATEGORY_ZH = {
    "earphones": "耳機",
    "phone": "手機",
    "wallet": "錢包",
    "keys": "鑰匙",
    "card": "卡片或證件",
    "umbrella": "雨傘",
    "drink": "瓶裝飲料",
    "bottle": "水壺或保溫瓶",
    "bag": "包包",
    "laptop": "筆記型電腦",
    "mouse": "滑鼠",
    "glasses": "眼鏡",
    "charger": "充電器或線材",
    "book": "書本或筆記本",
    "clothing": "衣物",
    "other": "個人物品",
}
COLOR_ZH = {
    "black": "黑色",
    "white": "白色",
    "blue": "藍色",
    "red": "紅色",
    "green": "綠色",
    "gray": "灰色或銀色",
    "pink": "粉紅色",
    "yellow": "黃色",
    "brown": "棕色",
    "orange": "橘色",
    "purple": "紫色",
    "transparent": "透明",
    "multicolor": "多色",
}
SHAPE_ZH = {
    "rectangular": "矩形或盒狀",
    "round": "圓形或橢圓形",
    "cylindrical": "圓柱形",
    "long": "細長形",
    "folded": "折疊外形",
    "irregular": "不規則外形",
}
FEATURE_ZH = {
    "plain": "素面",
    "patterned": "有明顯圖案",
    "text_or_logo": "有文字或標誌",
    "sticker": "有貼紙",
    "case": "附保護殼或外盒",
    "strap": "附帶子、掛繩或吊飾",
    "scratched": "有刮痕或使用痕跡",
}
MATERIAL_ZH = {
    "leather": "皮革或合成皮材質",
    "metal": "金屬材質",
    "plastic": "塑膠材質",
    "fabric": "布料或帆布材質",
    "rubber": "橡膠或矽膠材質",
    "glass": "玻璃材質",
}


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


class MultimodalAnalyzer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def analyze(
        self, description: str, image_bytes: bytes | None = None
    ) -> ItemAttributes:
        if self.settings.demo_mode:
            return await self._local_multimodal(description, image_bytes)
        try:
            attributes = await OllamaService(self.settings).extract_item(
                description, image_bytes
            )
            rules = self._rule_based(description, bool(image_bytes))
            attributes.category = attributes.category or rules.category
            attributes.color = attributes.color or rules.color
            attributes.brand = attributes.brand or rules.brand
            attributes.distinctive_features = list(
                dict.fromkeys(
                    [*attributes.distinctive_features, *rules.distinctive_features]
                )
            )
            return attributes
        except (httpx.HTTPError, RuntimeError, ValueError, json.JSONDecodeError):
            # Registration remains available if the local Ollama service is stopped.
            return await self._local_multimodal(description, image_bytes)

    async def _local_multimodal(
        self, description: str, image_bytes: bytes | None
    ) -> ItemAttributes:
        attributes = self._rule_based(description, bool(image_bytes))
        if not image_bytes:
            return attributes

        if self.settings.demo_mode:
            detected = self._basic_image_attributes(image_bytes)
        else:
            embeddings = get_embedding_service(self.settings)
            detected = {
                "category": await embeddings.best_siglip_label(
                    image_bytes, IMAGE_CATEGORY_PROMPTS
                ),
                "color": await embeddings.best_siglip_label(
                    image_bytes, IMAGE_COLOR_PROMPTS
                ),
                "shape": await embeddings.best_siglip_label(
                    image_bytes, IMAGE_SHAPE_PROMPTS
                ),
                "feature": await embeddings.best_siglip_label(
                    image_bytes, IMAGE_FEATURE_PROMPTS
                ),
                "material": await embeddings.best_siglip_label(
                    image_bytes, IMAGE_MATERIAL_PROMPTS
                ),
            }

        category = attributes.category or detected["category"]
        color = attributes.color or detected["color"]
        features = list(attributes.distinctive_features)
        shape = SHAPE_ZH.get(detected.get("shape", ""))
        feature = FEATURE_ZH.get(detected.get("feature", ""))
        material = MATERIAL_ZH.get(detected.get("material", ""))
        for value in (shape, feature, material):
            if value and value not in features:
                features.append(value)

        normalized = attributes.normalized_description.strip()
        generic_descriptions = {
            "",
            "拾獲者上傳的物品照片",
            "使用者上傳的拾獲物品照片",
            "使用者上傳的遺失物照片",
        }
        if normalized in generic_descriptions:
            summary = "".join((COLOR_ZH.get(color, ""), CATEGORY_ZH.get(category, "個人物品")))
            details = "、".join(features)
            normalized = f"AI 圖像辨識：{summary}" + (f"，特色為{details}" if details else "")
        return ItemAttributes(
            category=category,
            brand=attributes.brand,
            color=color,
            distinctive_features=features,
            normalized_description=normalized,
        )

    @staticmethod
    def _basic_image_attributes(image_bytes: bytes) -> dict[str, str]:
        from PIL import Image, ImageStat

        with Image.open(io.BytesIO(image_bytes)) as source:
            image = source.convert("RGB")
            image.thumbnail((128, 128))
            red, green, blue = ImageStat.Stat(image).median
            width, height = image.size
        maximum, minimum = max(red, green, blue), min(red, green, blue)
        if maximum < 60:
            color = "black"
        elif minimum > 205:
            color = "white"
        elif maximum - minimum < 25:
            color = "gray"
        elif red == maximum and green > blue * 1.2:
            color = "orange" if green > 90 else "red"
        elif red == maximum:
            color = "red"
        elif green == maximum:
            color = "green"
        else:
            color = "blue"
        ratio = width / max(height, 1)
        shape = "long" if ratio > 1.8 or ratio < 0.55 else "rectangular"
        return {
            "category": "other",
            "color": color,
            "shape": shape,
            "feature": "plain",
        }

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
        text_features = {
            "牛皮": "牛皮材質",
            "真皮": "真皮材質",
            "皮革": "皮革材質",
            "帆布": "帆布材質",
            "布料": "布料材質",
            "金屬": "金屬材質",
            "塑膠": "塑膠材質",
            "矽膠": "矽膠材質",
            "拉鍊": "拉鍊開合",
            "磁扣": "磁扣開合",
            "摺疊": "可摺疊",
            "折疊": "可折疊",
            "素面": "素面",
            "紋路": "有表面紋路",
            "logo": "有品牌標誌",
            "標誌": "有品牌標誌",
        }
        for term, feature in text_features.items():
            if term in lowered and feature not in features:
                features.append(feature)
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

class EmbeddingService:
    dimension = 768

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._e5 = None
        self._siglip_model = None
        self._siglip_processor = None
        self._device = None
        self._siglip_text_cache: dict[tuple[str, ...], np.ndarray] = {}

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

    async def best_siglip_label(
        self, image_bytes: bytes, labels: dict[str, str]
    ) -> str:
        keys = tuple(labels)
        prompts = tuple(labels.values())
        image_vector = np.asarray(
            await self.encode_siglip_image(image_bytes), dtype=np.float32
        )
        text_vectors = self._siglip_text_cache.get(prompts)
        if text_vectors is None:
            self._load_siglip()
            import torch

            inputs = self._siglip_processor(
                text=list(prompts),
                padding="max_length",
                truncation=True,
                return_tensors="pt",
            )
            inputs = {
                name: value.to(self._get_device()) for name, value in inputs.items()
            }
            with torch.inference_mode():
                output = self._siglip_model.get_text_features(**inputs)
                matrix = self._feature_matrix(output)
                matrix = matrix / matrix.norm(p=2, dim=1, keepdim=True)
            text_vectors = matrix.cpu().float().numpy()
            self._siglip_text_cache[prompts] = text_vectors
        scores = text_vectors @ image_vector
        return keys[int(np.argmax(scores))]

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
        return EmbeddingService._feature_matrix(output)[0]

    @staticmethod
    def _feature_matrix(output):
        pooled = getattr(output, "pooler_output", None)
        if pooled is not None:
            return pooled
        if getattr(output, "ndim", 0) == 2:
            return output
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
