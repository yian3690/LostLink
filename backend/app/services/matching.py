import math
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


def calibrated_siglip_score(raw_cosine: float) -> float:
    """Map SigLIP 2 cosine values to a useful 0..1 retrieval confidence signal."""
    if raw_cosine <= 0.05:
        return 0.0
    return round(min(1.0, (raw_cosine - 0.05) / 0.20), 6)


def location_score(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0.0
    a, b = left.casefold(), right.casefold()
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.85
    tokens_a = set(a.replace("樓", "f").split())
    tokens_b = set(b.replace("樓", "f").split())
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
    category_brand = 0.7 * category + 0.3 * brand
    color = equal_score(lost.color, found.color)
    place = location_score(lost.location, found.location)
    time = temporal_score(lost.occurred_at, found.occurred_at)

    breakdown = {
        "cross_modal": round(cross_modal, 4),
        "cross_modal_raw": round(cross_modal_raw, 4),
        "text": round(text, 4),
        "category_brand": round(category_brand, 4),
        "color": round(color, 4),
        "location": round(place, 4),
        "time": round(time, 4),
    }
    score = (
        0.35 * cross_modal
        + 0.25 * text
        + 0.15 * category_brand
        + 0.10 * color
        + 0.10 * place
        + 0.05 * time
    )
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

    return MatchResult(
        score=round(score, 4),
        decision=decision,
        breakdown=breakdown,
        reasons=reasons,
    )
