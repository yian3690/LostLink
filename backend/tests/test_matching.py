from datetime import datetime, timezone

from app.models.entities import ItemEmbedding, ItemReport
from app.services.matching import calibrated_siglip_score, score_reports


def report(kind: str, vector: list[float], **values) -> ItemReport:
    item = ItemReport(
        kind=kind,
        status="open",
        description=values.get("description", ""),
        category=values.get("category"),
        brand=values.get("brand"),
        color=values.get("color"),
        location=values.get("location"),
        occurred_at=values.get("occurred_at"),
    )
    item.embedding = ItemEmbedding(
        e5_text=vector,
        siglip_text=vector,
        siglip_image=vector if kind == "found" else None,
    )
    return item


def test_matching_identical_signals_notifies() -> None:
    now = datetime.now(timezone.utc)
    lost = report(
        "lost",
        [1.0, 0.0],
        category="earphones",
        brand="apple",
        color="black",
        location="圖書館 3F",
        occurred_at=now,
    )
    found = report(
        "found",
        [1.0, 0.0],
        category="earphones",
        brand="apple",
        color="black",
        location="圖書館 3F",
        occurred_at=now,
    )

    result = score_reports(lost, found)

    assert result.score == 1.0
    assert result.decision == "notify"
    assert "物品類別相符" in result.reasons


def test_matching_unrelated_items_waits() -> None:
    lost = report(
        "lost",
        [1.0, 0.0],
        category="earphones",
        color="black",
        location="圖書館",
    )
    found = report(
        "found",
        [0.0, 1.0],
        category="umbrella",
        color="red",
        location="體育館",
    )

    result = score_reports(lost, found)

    assert result.score == 0.0
    assert result.decision == "waiting"


def test_siglip_cosine_is_calibrated_for_retrieval() -> None:
    assert calibrated_siglip_score(0.05) == 0.0
    assert calibrated_siglip_score(0.15) == 0.5
    assert calibrated_siglip_score(0.25) == 1.0
