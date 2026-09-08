import asyncio

from app.core.config import Settings
from app.services.ai import MultimodalAnalyzer


def test_rule_based_analyzer_detects_yellow_drink() -> None:
    attributes = asyncio.run(
        MultimodalAnalyzer(Settings(demo_mode=True)).analyze(
            "我的黃色飲料在 ZB302 教室不見了"
        )
    )
    assert attributes.category == "drink"
    assert attributes.color == "yellow"
