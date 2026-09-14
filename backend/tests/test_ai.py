import asyncio

from app.core.config import Settings
from app.schemas.reports import ItemAttributes
from app.services.ai import (
    MultimodalAnalyzer,
    clean_distinctive_features,
    clean_feature_evidence,
    detect_attribute_conflicts,
)
from app.services.ollama import OllamaService
from app.services.item_lookup import safe_lookup_terms


def test_web_lookup_uses_only_safe_brand_and_visible_text() -> None:
    assert safe_lookup_terms(
        "CeraVe",
        ["Moisturising Lotion", "user@example.com", "https://private.test", "0912345678"],
    ) == ["CeraVe", "Moisturising Lotion"]


def test_distinctive_features_remove_structured_duplicates_and_generic_terms() -> None:
    assert clean_distinctive_features(
        [
            "黑色",
            "Apple",
            "耳機",
            "AirPods",
            "有文字",
            "充電盒為圓角矩形",
            "充電盒為圓角矩形",
            "盒蓋正面有白色刮痕",
        ],
        category="earphones",
        color="black",
        brand="apple",
    ) == ["AirPods", "充電盒為圓角矩形", "盒蓋正面有白色刮痕"]


def test_low_confidence_features_are_not_persisted() -> None:
    attributes = ItemAttributes(
        category="umbrella",
        color="black",
        distinctive_features=["傘柄為彎曲木質握把", "傘面似乎有磨損", "傘長約 30 公分"],
        feature_confidences={
            "傘柄為彎曲木質握把": 0.91,
            "傘面似乎有磨損": 0.43,
            "傘長約 30 公分": 0.75,
        },
        recognition_confidence=0.88,
    )
    features, confidences = clean_feature_evidence(attributes)
    assert features == ["傘柄為彎曲木質握把"]
    assert confidences == {"傘柄為彎曲木質握把": 0.91}


def test_unsupported_precise_measurement_requests_retry() -> None:
    attributes = ItemAttributes(
        category="foam_roller",
        normalized_description="黑色按摩滾筒，長度約 30 公分。",
        distinctive_features=["長度約 30 公分"],
    )
    assert any("30 公分" in value for value in detect_attribute_conflicts(attributes))
    assert not detect_attribute_conflicts(attributes, "按摩滾筒長約 30 公分")


def test_user_supplied_ml_measurement_is_not_treated_as_meters() -> None:
    attributes = ItemAttributes(
        category="bottle",
        normalized_description="黑色水壺，容量 700 ml。",
        distinctive_features=["容量 700 ml"],
    )
    assert not detect_attribute_conflicts(attributes, "黑色 700 ml 水壺")


def test_untrusted_background_text_and_features_are_removed() -> None:
    analyzer = MultimodalAnalyzer(Settings())
    attributes = ItemAttributes(
        category="foam_roller",
        color="black",
        distinctive_features=["印有 WHT GOLD STANDARD 字樣", "表面有凸起紋理"],
        feature_confidences={
            "印有 WHT GOLD STANDARD 字樣": 0.96,
            "表面有凸起紋理": 0.91,
        },
        visible_text=["WHT GOLD STANDARD"],
        recognition_confidence=0.92,
    )
    rules = ItemAttributes(
        category="foam_roller",
        normalized_description="按摩滾筒",
        recognition_confidence=0.98,
    )

    merged = analyzer._merge_with_explicit_rules(attributes, rules)

    assert merged.visible_text == []
    assert merged.distinctive_features == ["表面有凸起紋理"]
    assert merged.feature_confidences == {"表面有凸起紋理": 0.91}


def test_category_and_primary_color_conflicts_are_detected() -> None:
    attributes = ItemAttributes(
        category="charger",
        color="black",
        normalized_description="黑色充電盒，傘面為深藍色。",
    )
    conflicts = detect_attribute_conflicts(attributes)
    assert any("耳機" in conflict for conflict in conflicts)
    assert any("藍色" in conflict for conflict in conflicts)


def test_ollama_item_analysis_runs_classification_before_details(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_structured_chat(self, prompt, image_bytes, response_format, system_prompt):
        calls.append(system_prompt)
        if "類別判斷器" in system_prompt:
            return '{"category":"earphones","recognition_confidence":0.92,"item_name_candidates":["無線耳機"]}'
        return (
            '{"category":"earphones","brand":"Apple","color":"black",'
            '"distinctive_features":["充電盒為圓角矩形"],'
            '"feature_confidences":{"充電盒為圓角矩形":0.9},'
            '"normalized_description":"黑色無線耳機與充電盒。",'
            '"recognition_confidence":0.92,"item_name_candidates":["無線耳機"],'
            '"visible_text":[]}'
        )

    monkeypatch.setattr(OllamaService, "_structured_chat", fake_structured_chat)
    attributes = asyncio.run(
        OllamaService(Settings()).extract_item("黑色耳機", b"image")
    )
    assert attributes.category == "earphones"
    assert len(calls) == 2
    assert "類別判斷器" in calls[0]
    assert "特徵抽取器" in calls[1]


def test_clear_text_uses_one_constrained_detail_call(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_details(
        self, description, classification, image_bytes=None, correction_context=None
    ):
        calls.append("details")
        return ItemAttributes(
            category=classification.category,
            color="beige",
            normalized_description="米色雨傘",
            recognition_confidence=classification.recognition_confidence,
            item_name_candidates=classification.item_name_candidates,
        )

    async def unexpected_two_stage(self, *args, **kwargs):
        calls.append("two-stage")
        raise AssertionError("clear text must not call the two-stage extractor")

    monkeypatch.setattr(OllamaService, "extract_item_details", fake_details)
    monkeypatch.setattr(OllamaService, "extract_item", unexpected_two_stage)

    attributes = asyncio.run(
        MultimodalAnalyzer(Settings(demo_mode=False)).analyze("我的米色雨傘不見了")
    )

    assert calls == ["details"]
    assert attributes.category == "umbrella"
    assert attributes.color == "beige"


def test_rule_based_analyzer_detects_yellow_drink() -> None:
    attributes = asyncio.run(
        MultimodalAnalyzer(Settings(demo_mode=True)).analyze(
            "我的黃色飲料在 ZB302 教室不見了"
        )
    )
    assert attributes.category == "drink"
    assert attributes.color == "yellow"


def test_rule_based_analyzer_treats_thermos_cup_as_bottle() -> None:
    analyzer = MultimodalAnalyzer(
        Settings(database_url="sqlite+aiosqlite:///:memory:")
    )
    attributes = analyzer._rule_based("黑色保溫杯", has_image=False)

    assert attributes.category == "bottle"
    assert attributes.color == "black"
