import hashlib
import io
import json
import math
import re
from functools import lru_cache

import httpx
import numpy as np

from app.core.config import Settings
from app.schemas.reports import ItemAttributes, ItemClassification
from app.services.ollama import OllamaService
from app.services.item_lookup import TextOnlyItemLookup


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
    "mouse": ("滑鼠", "mouse"),
    "glasses": ("眼鏡", "墨鏡", "太陽眼鏡"),
    "charger": ("充電器", "充電線", "傳輸線", "電源線", "變壓器"),
    "book": ("書本", "課本", "筆記本", "講義"),
    "clothing": ("衣服", "外套", "帽子", "圍巾", "衣物"),
    "foam_roller": ("按摩滾筒", "泡棉滾筒", "滾筒", "按摩滾輪", "瑜珈柱", "瑜伽柱", "狼牙棒"),
    "toiletries": ("乳液", "洗面乳", "洗髮精", "護手霜", "化妝品", "保養品"),
    "stationery": ("鉛筆盒", "筆袋", "文具", "原子筆", "自動筆"),
    "watch": ("手錶", "智慧手錶"),
    "jewelry": ("項鍊", "手鍊", "戒指", "耳環", "飾品"),
    "shoes": ("鞋子", "球鞋", "拖鞋"),
    "helmet": ("安全帽", "頭盔"),
    "ball": ("籃球", "排球", "足球", "球類"),
    "toy": ("玩偶", "娃娃", "玩具"),
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
    "beige": ("米色", "奶油色", "beige"),
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
    "foam_roller": "a clear photo of a foam roller or massage roller",
    "toiletries": "a clear photo of lotion, cosmetics, or toiletries",
    "stationery": "a clear photo of stationery or a pencil case",
    "watch": "a clear photo of a wristwatch or smartwatch",
    "jewelry": "a clear photo of jewelry",
    "shoes": "a clear photo of shoes or footwear",
    "helmet": "a clear photo of a helmet",
    "ball": "a clear photo of a sports ball",
    "toy": "a clear photo of a toy or plush doll",
    "personal_item": "a clear photo of another personal belonging",
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
    "foam_roller": "按摩滾筒",
    "toiletries": "盥洗或保養用品",
    "stationery": "文具",
    "watch": "手錶",
    "jewelry": "飾品",
    "shoes": "鞋子",
    "helmet": "安全帽",
    "ball": "球類",
    "toy": "玩偶或玩具",
    "personal_item": "其他個人物品",
    "other": "其他個人物品",
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
    "beige": "米色",
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


def normalize_category_value(value: str | None, description: str = "") -> str | None:
    """Store stable category codes while accepting Chinese admin input and legacy `other`."""
    raw = (value or "").strip()
    lowered = raw.casefold()
    if lowered in IMAGE_CATEGORY_PROMPTS:
        if lowered not in {"other", "personal_item"}:
            return lowered
    combined = "；".join(part for part in (raw, description) if part)
    inferred = next(
        (name for name, terms in CATEGORY_TERMS.items() if _contains(combined, terms)),
        None,
    )
    if inferred:
        return inferred
    if raw and any("\u4e00" <= char <= "\u9fff" for char in raw):
        return raw
    return "personal_item" if raw or description.strip() else None


def normalize_color_value(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    lowered = raw.casefold()
    if lowered in IMAGE_COLOR_PROMPTS:
        return lowered
    return next(
        (name for name, terms in COLOR_TERMS.items() if _contains(raw, terms)),
        raw if any("\u4e00" <= char <= "\u9fff" for char in raw) else None,
    )


def category_name_zh(category: str | None) -> str:
    raw = (category or "").strip()
    if not raw:
        return "待補充物品名稱"
    if raw in CATEGORY_ZH:
        return CATEGORY_ZH[raw]
    if any("\u4e00" <= char <= "\u9fff" for char in raw):
        return raw
    return "待補充物品名稱"


def _feature_key(value: str) -> str:
    return re.sub(r"[\s、，,。．·•:：;；/_-]+", "", value).casefold()


def clean_distinctive_features(
    features: list[str],
    category: str | None,
    color: str | None,
    brand: str | None,
    limit: int = 6,
) -> list[str]:
    """Keep concise, unique evidence that is not already a structured field."""

    excluded = {
        _feature_key(value)
        for value in (
            category or "",
            category_name_zh(category),
            color or "",
            COLOR_ZH.get(color or "", ""),
            brand or "",
        )
        if value
    }
    generic = {
        _feature_key(value)
        for value in ("有文字", "有標誌", "有logo", "有品牌", "物品", "看起來正常")
    }
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in features:
        feature = re.sub(r"\s+", " ", value).strip(" 、，,。．;；")
        normalized = _feature_key(feature)
        if not feature or normalized in excluded or normalized in generic or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(feature[:80])
        if len(cleaned) >= limit:
            break
    return cleaned


def clean_feature_evidence(
    attributes: ItemAttributes,
    minimum_confidence: float = 0.80,
    limit: int = 6,
) -> tuple[list[str], dict[str, float]]:
    """Filter uncertain evidence and align confidence keys with cleaned features."""

    confidence_by_key = {
        _feature_key(name): max(0.0, min(1.0, float(confidence)))
        for name, confidence in attributes.feature_confidences.items()
        if name.strip()
    }
    features: list[str] = []
    confidences: dict[str, float] = {}
    for feature in clean_distinctive_features(
        attributes.distinctive_features,
        attributes.category,
        attributes.color,
        attributes.brand,
        limit=30,
    ):
        confidence = confidence_by_key.get(_feature_key(feature), 0.0)
        if confidence < minimum_confidence:
            continue
        features.append(feature)
        confidences[feature] = round(confidence, 3)
        if len(features) >= limit:
            break
    return features, confidences


def detect_attribute_conflicts(
    attributes: ItemAttributes, original_description: str = ""
) -> list[str]:
    """Detect strong category or primary-color contradictions before persistence."""

    text = "；".join(
        [attributes.normalized_description, *attributes.distinctive_features]
    ).casefold()
    category_hits = {
        category
        for category, terms in CATEGORY_TERMS.items()
        if _contains(text, terms)
    }
    if "充電盒" in text:
        category_hits.add("earphones")
    conflicts: list[str] = []
    supplied_text = "；".join([original_description, *attributes.visible_text]).casefold()
    precision_pattern = re.compile(
        r"\d+(?:\.\d+)?\s*(?:毫升|ml|公升|公斤|kg|公分|厘米|cm|毫米|mm|公尺|克|m|l)",
        flags=re.IGNORECASE,
    )
    unsupported_measurements = {
        match.group(0)
        for match in precision_pattern.finditer(text)
        if _feature_key(match.group(0)) not in _feature_key(supplied_text)
    }
    if unsupported_measurements:
        conflicts.append(
            "照片缺少比例尺或標示，不可猜測精確尺寸："
            + "、".join(sorted(unsupported_measurements))
        )
    if attributes.category in CATEGORY_TERMS:
        other_categories = category_hits - {attributes.category}
        if other_categories:
            names = "、".join(category_name_zh(value) for value in sorted(other_categories))
            conflicts.append(
                f"類別為{category_name_zh(attributes.category)}，描述卻同時出現{names}"
            )

    if attributes.color in COLOR_TERMS:
        first_clause = re.split(r"[，。；]", text, maxsplit=1)[0]
        asserted_colors = {
            color
            for color, terms in COLOR_TERMS.items()
            if _contains(first_clause, terms)
        }
        for color, terms in COLOR_TERMS.items():
            for term in terms:
                if re.search(
                    rf"(?:整體|主色|外觀|本體|傘面|瓶身|外殼)(?:主要)?(?:為|呈現|是)"
                    rf"[^，。；]{{0,4}}{re.escape(term)}",
                    text,
                ):
                    asserted_colors.add(color)
        other_colors = asserted_colors - {attributes.color}
        if other_colors:
            names = "、".join(COLOR_ZH.get(value, value) for value in sorted(other_colors))
            conflicts.append(
                f"主色為{COLOR_ZH.get(attributes.color, attributes.color)}，描述卻宣稱主要表面為{names}"
            )
    return conflicts


def item_confirmation_candidates(attributes: ItemAttributes) -> list[str]:
    values = [*attributes.item_name_candidates]
    category = category_name_zh(attributes.category)
    category_is_covered = any(
        value.strip() and (value.strip() in category or category in value.strip())
        for value in values
    )
    if category not in {"待補充物品名稱", "其他個人物品"} and not category_is_covered:
        values.insert(0, category)
    cleaned: list[str] = []
    for value in values:
        name = value.strip()
        if not name or name.casefold() in {"other", "personal_item", "其他", "其他個人物品"}:
            continue
        if name not in cleaned:
            cleaned.append(name)
    return cleaned[:3]


def needs_item_confirmation(
    attributes: ItemAttributes,
    description: str,
    has_image: bool,
    threshold: float,
) -> bool:
    if not has_image:
        return False
    explicit_item = any(
        _contains(description, terms) for terms in CATEGORY_TERMS.values()
    )
    if explicit_item:
        return False
    confidence = attributes.recognition_confidence
    return (
        attributes.category in {None, "other", "personal_item"}
        or confidence is None
        or confidence < threshold
    )


class MultimodalAnalyzer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def _merge_with_explicit_rules(
        attributes: ItemAttributes, rules: ItemAttributes
    ) -> ItemAttributes:
        attributes.category = normalize_category_value(
            rules.category or attributes.category,
            attributes.normalized_description,
        )
        attributes.color = normalize_color_value(rules.color or attributes.color)
        attributes.brand = attributes.brand or rules.brand
        attributes.distinctive_features = list(
            dict.fromkeys([*attributes.distinctive_features, *rules.distinctive_features])
        )
        for feature in rules.distinctive_features:
            attributes.feature_confidences[feature] = 0.98
        if rules.category:
            attributes.recognition_confidence = 0.98
            attributes.item_name_candidates = [category_name_zh(rules.category)]
        elif not attributes.item_name_candidates and attributes.category:
            attributes.item_name_candidates = [category_name_zh(attributes.category)]
        attributes.item_name_candidates = item_confirmation_candidates(attributes)
        visible_text = list(
            dict.fromkeys(value.strip() for value in attributes.visible_text if value.strip())
        )[:20]
        trusted_source = rules.normalized_description.casefold()
        trusted_visible_text: list[str] = []
        for value in visible_text:
            visible_key = _feature_key(value)
            brand_key = _feature_key(attributes.brand or "")
            is_trusted = bool(
                visible_key
                and (
                    visible_key in _feature_key(trusted_source)
                    or (brand_key and (visible_key in brand_key or brand_key in visible_key))
                )
            )
            if is_trusted:
                trusted_visible_text.append(value)
                continue
            for feature in attributes.distinctive_features:
                if visible_key and visible_key in _feature_key(feature):
                    attributes.feature_confidences[feature] = min(
                        attributes.feature_confidences.get(feature, 0.0),
                        0.67,
                    )
        attributes.visible_text = trusted_visible_text
        features, confidences = clean_feature_evidence(attributes)
        attributes.distinctive_features = features
        attributes.feature_confidences = confidences
        return attributes

    @staticmethod
    def _grounded_image_description(attributes: ItemAttributes) -> str:
        item_name = "".join(
            (COLOR_ZH.get(attributes.color or "", ""), category_name_zh(attributes.category))
        )
        name = f"{attributes.brand} {item_name}" if attributes.brand else item_name
        details = "、".join(attributes.distinctive_features)
        return name + (f"，可確認特徵為{details}。" if details else "。")

    async def analyze(
        self, description: str, image_bytes: bytes | None = None
    ) -> ItemAttributes:
        if self.settings.demo_mode:
            return await self._local_multimodal(description, image_bytes)
        try:
            ollama = OllamaService(self.settings)
            rules = self._rule_based(description, bool(image_bytes))
            if image_bytes is None and rules.category:
                # Clear text already establishes the main category. Skip the
                # separate classifier and use one constrained detail call.
                classification = ItemClassification(
                    category=rules.category,
                    recognition_confidence=0.98,
                    item_name_candidates=[category_name_zh(rules.category)],
                )
                attributes = await ollama.extract_item_details(
                    description,
                    classification,
                )
            else:
                # Photos and unclear text still use category-first analysis.
                attributes = await ollama.extract_item(description, image_bytes)
            # Explicit words in the user's description are more reliable than
            # a visual guess (for example「滾筒」must not become bottle).
            attributes = self._merge_with_explicit_rules(attributes, rules)
            conflicts = detect_attribute_conflicts(attributes, description)
            if conflicts and image_bytes:
                retried = await ollama.extract_item(
                    description,
                    image_bytes,
                    correction_context=conflicts,
                )
                attributes = self._merge_with_explicit_rules(retried, rules)
                remaining_conflicts = detect_attribute_conflicts(attributes, description)
                if remaining_conflicts:
                    attributes.recognition_confidence = min(
                        attributes.recognition_confidence or 0.59,
                        0.59,
                    )
            if image_bytes:
                attributes.normalized_description = self._grounded_image_description(
                    attributes
                )
            if needs_item_confirmation(
                attributes,
                description,
                bool(image_bytes),
                self.settings.item_confirmation_threshold,
            ) and (attributes.brand or attributes.visible_text):
                evidence = await TextOnlyItemLookup(self.settings).search(
                    attributes.brand, attributes.visible_text
                )
                if evidence:
                    refined = await ollama.refine_item_candidates(
                        attributes.brand,
                        attributes.visible_text,
                        attributes.item_name_candidates,
                        evidence,
                    )
                    if refined:
                        attributes.item_name_candidates = refined
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

        category = normalize_category_value(
            attributes.category or detected["category"],
            attributes.normalized_description or description,
        )
        color = normalize_color_value(attributes.color or detected["color"])
        features = list(attributes.distinctive_features)
        shape = SHAPE_ZH.get(detected.get("shape", ""))
        feature = FEATURE_ZH.get(detected.get("feature", ""))
        material = MATERIAL_ZH.get(detected.get("material", ""))
        for value in (shape, feature, material):
            if value and value not in features:
                features.append(value)
                attributes.feature_confidences.setdefault(value, 0.82)

        normalized = attributes.normalized_description.strip()
        generic_descriptions = {
            "",
            "拾獲者上傳的物品照片",
            "使用者上傳的拾獲物品照片",
            "使用者上傳的遺失物照片",
        }
        if normalized in generic_descriptions:
            summary = "".join((COLOR_ZH.get(color, ""), category_name_zh(category)))
            details = "、".join(features)
            normalized = f"AI 圖像辨識：{summary}" + (f"，特色為{details}" if details else "")
        local_result = ItemAttributes(
            category=category,
            brand=attributes.brand,
            color=color,
            distinctive_features=features,
            feature_confidences=attributes.feature_confidences,
            normalized_description=normalized,
            recognition_confidence=(
                attributes.recognition_confidence
                if attributes.recognition_confidence is not None
                else (0.98 if attributes.category else 0.35)
            ),
            item_name_candidates=item_confirmation_candidates(attributes),
            visible_text=attributes.visible_text,
        )
        cleaned_features, confidences = clean_feature_evidence(local_result)
        local_result.distinctive_features = cleaned_features
        local_result.feature_confidences = confidences
        return local_result

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
            "category": "personal_item",
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
            feature_confidences={feature: 0.98 for feature in features},
            normalized_description=normalized,
            recognition_confidence=0.98 if category else None,
            item_name_candidates=[category_name_zh(category)] if category else [],
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
