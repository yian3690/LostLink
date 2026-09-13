import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from app.models.entities import ItemReport


@dataclass(slots=True)
class MatchResult:
    score: float
    decision: str
    breakdown: dict[str, float]
    reasons: list[str]


def cosine(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right:
        return 0.0
    a = np.asarray(left, dtype=np.float32)
    b = np.asarray(right, dtype=np.float32)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0:
        return 0.0
    return max(0.0, min(1.0, float(np.dot(a, b) / denominator)))


def equal_score(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0.0
    return 1.0 if left.casefold() == right.casefold() else 0.0


def normalized_description(value: str | None) -> str:
    """Normalize harmless formatting differences before exact-text comparison."""
    if not value:
        return ""
    return re.sub(r"[\W_]+", "", value.casefold(), flags=re.UNICODE)


def container_subtype(report: ItemReport) -> str | None:
    """Distinguish reusable drinkware from disposable beverage containers."""
    if report.category not in {"bottle", "drink"}:
        return None
    text = " ".join(
        [report.description or "", *(report.distinctive_features or [])]
    ).casefold()
    reusable_terms = (
        "水壺", "水杯", "保溫杯", "保溫瓶", "隨行杯", "運動水瓶",
        "reusable", "water bottle", "thermos", "vacuum flask", "insulated bottle",
    )
    disposable_terms = (
        "寶特瓶", "瓶裝飲料", "手搖飲", "茶飲", "礦泉水", "飲料瓶",
        "disposable", "beverage bottle", "soft drink bottle",
    )
    reusable = any(term in text for term in reusable_terms)
    disposable = any(term in text for term in disposable_terms)
    if reusable and not disposable:
        return "reusable"
    if disposable and not reusable:
        return "disposable"
    return None


def calibrated_siglip_score(raw_cosine: float) -> float:
    """Map SigLIP 2 cosine values to a useful 0..1 retrieval confidence signal."""
    if raw_cosine <= 0.05:
        return 0.0
    return round(min(1.0, (raw_cosine - 0.05) / 0.20), 6)


def location_score(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0.0

    def normalize(value: str) -> str:
        normalized = (
            value.casefold()
            .replace("學餐", "學生餐廳")
            .replace("綜大", "綜合大樓")
            .replace("教大", "教學大樓")
        )
        chinese_floors = {
            "十二": "12",
            "十一": "11",
            "十": "10",
            "九": "9",
            "八": "8",
            "七": "7",
            "六": "6",
            "五": "5",
            "四": "4",
            "三": "3",
            "二": "2",
            "一": "1",
        }
        for label, number in chinese_floors.items():
            normalized = normalized.replace(f"{label}樓", f"{number}f")
        normalized = re.sub(r"(\d+)\s*樓", r"\1f", normalized)
        # Campus room numbers normally encode their floor (301 -> 3F,
        # 1201 -> 12F). Add that as a searchable token without discarding the
        # original room number.
        for room in re.findall(r"(?<!\d)(\d{3,4})\s*教室", normalized):
            floor = int(room) // 100
            if floor > 0:
                normalized += f" {floor}f"
        return normalized

    a, b = normalize(left), normalize(right)
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.85
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    overlap = len(tokens_a & tokens_b)
    return overlap / max(1, len(tokens_a | tokens_b))


def temporal_score(left: datetime | None, right: datetime | None) -> float:
    if not left or not right:
        return 0.0
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    hours = abs((left - right).total_seconds()) / 3600
    return math.exp(-hours / 24)


def score_reports(
    lost: ItemReport,
    found: ItemReport,
    notify_threshold: float = 0.82,
    review_threshold: float = 0.68,
) -> MatchResult:
    lost_embedding = lost.embedding
    found_embedding = found.embedding

    text = cosine(
        lost_embedding.e5_text if lost_embedding else None,
        found_embedding.e5_text if found_embedding else None,
    )
    cross_modal_raw = cosine(
        lost_embedding.siglip_text if lost_embedding else None,
        (
            found_embedding.siglip_image
            if found_embedding and found_embedding.siglip_image
            else found_embedding.siglip_text if found_embedding else None
        ),
    )
    cross_modal = calibrated_siglip_score(cross_modal_raw)
    category = equal_score(lost.category, found.category)
    brand = equal_score(lost.brand, found.brand)
    category_brand_parts: list[tuple[float, float]] = []
    if lost.category and found.category:
        category_brand_parts.append((0.7, category))
    if lost.brand and found.brand:
        category_brand_parts.append((0.3, brand))
    category_brand_weight = sum(weight for weight, _ in category_brand_parts)
    category_brand = (
        sum(weight * value for weight, value in category_brand_parts)
        / category_brand_weight
        if category_brand_weight
        else 0.0
    )
    color = equal_score(lost.color, found.color)
    place = location_score(lost.location, found.location)
    time = temporal_score(lost.occurred_at, found.occurred_at)
    lost_description = normalized_description(lost.description)
    found_description = normalized_description(found.description)
    exact_description = bool(
        lost_description
        and found_description
        and lost_description == found_description
    )
    lost_container_subtype = container_subtype(lost)
    found_container_subtype = container_subtype(found)
    container_subtype_conflict = bool(
        lost_container_subtype
        and found_container_subtype
        and lost_container_subtype != found_container_subtype
    )

    breakdown = {
        "cross_modal": round(cross_modal, 4),
        "cross_modal_raw": round(cross_modal_raw, 4),
        "text": round(text, 4),
        "category_brand": round(category_brand, 4),
        "color": round(color, 4),
        "location": round(place, 4),
        "time": round(time, 4),
        "exact_description": 1.0 if exact_description else 0.0,
        "container_subtype_conflict": 1.0 if container_subtype_conflict else 0.0,
    }

    # Missing information is unknown rather than a mismatch. Only signals that
    # both reports can actually provide participate in the denominator.
    weighted_signals: list[tuple[float, float]] = []
    if (
        lost_embedding
        and lost_embedding.siglip_text
        and found_embedding
        and (found_embedding.siglip_image or found_embedding.siglip_text)
    ):
        weighted_signals.append((0.35, cross_modal))
    if (
        lost_embedding
        and lost_embedding.e5_text
        and found_embedding
        and found_embedding.e5_text
    ):
        weighted_signals.append((0.25, text))
    if category_brand_parts:
        weighted_signals.append((0.15, category_brand))
    if lost.color and found.color:
        weighted_signals.append((0.10, color))
    if lost.location and found.location:
        weighted_signals.append((0.10, place))
    if lost.occurred_at and found.occurred_at:
        weighted_signals.append((0.05, time))

    available_weight = sum(weight for weight, _ in weighted_signals)
    score = (
        sum(weight * value for weight, value in weighted_signals) / available_weight
        if available_weight
        else 0.0
    )
    breakdown["available_weight"] = round(available_weight, 4)

    # An identical, non-empty description with no category/color conflict is
    # stronger evidence than text-to-image similarity alone. Keep it below 1.0
    # because ownership must still be confirmed by the claimant.
    category_conflict = bool(lost.category and found.category and not category)
    color_conflict = bool(lost.color and found.color and not color)
    if exact_description and not category_conflict and not color_conflict:
        score = max(score, 0.90)
    if container_subtype_conflict:
        # A reusable bottle and a disposable beverage bottle must never become
        # a high-confidence match merely because both are cylindrical.
        score = min(score, 0.35)
    if score >= notify_threshold:
        decision = "notify"
    elif score >= review_threshold:
        decision = "review"
    else:
        decision = "waiting"

    reasons: list[str] = []
    if category:
        reasons.append("物品類別相符")
    if brand:
        reasons.append("品牌相符")
    if color:
        reasons.append("顏色相符")
    if place >= 0.75:
        reasons.append("地點接近")
    if time >= 0.75:
        reasons.append("時間接近")
    if cross_modal >= 0.75:
        reasons.append("照片與描述高度相似")
    if text >= 0.75:
        reasons.append("文字描述高度相似")
    if exact_description:
        reasons.append("完整描述一致")

    return MatchResult(
        score=round(score, 4),
        decision=decision,
        breakdown=breakdown,
        reasons=reasons,
    )
