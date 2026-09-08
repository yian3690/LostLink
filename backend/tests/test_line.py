import base64
import hashlib
import hmac
from types import SimpleNamespace

from app.core.config import Settings
from app.services.line import (
    LineClient,
    build_match_messages,
    build_match_reply,
    extract_location,
    parse_report_kind,
)


def test_line_signature_verification() -> None:
    settings = Settings(
        demo_mode=False,
        line_channel_secret="secret",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    body = b'{"events":[]}'
    signature = base64.b64encode(
        hmac.new(b"secret", body, hashlib.sha256).digest()
    ).decode("ascii")

    assert LineClient(settings).verify_signature(body, signature)
    assert not LineClient(settings).verify_signature(body, "invalid")


def test_parse_report_kind() -> None:
    assert parse_report_kind("遺失 我的黑色 AirPods") == (
        "lost",
        "我的黑色 AirPods",
    )
    assert parse_report_kind("拾獲：一把雨傘") == ("found", "一把雨傘")
    assert parse_report_kind("你好") == (None, "你好")
    assert parse_report_kind("我的黑色 AirPods 在圖書館不見了")[0] == "lost"
    assert parse_report_kind("我在二樓撿到一副黑色耳機")[0] == "found"
    assert parse_report_kind("我有撿到這個") == ("found", "這個")
    assert parse_report_kind("我有遺失這個") == ("lost", "這個")
    assert parse_report_kind("你能列目前的遺失物嗎")[0] is None
    assert extract_location("我在ZB302教室有東西不見") == "ZB302教室"
    assert extract_location("我的耳機在圖書館三樓不見了") == "圖書館 三F"


def test_match_reply_reports_found_and_not_found() -> None:
    no_match = build_match_reply("12345678-abcd", "lost", [])
    assert "目前還沒有找到相似物品" in no_match

    match = SimpleNamespace(score=0.93, reasons=["顏色相符", "地點接近"])
    found = build_match_reply("12345678-abcd", "lost", [match])
    assert "找到 1 個可能相似的拾獲物" in found
    assert "最高相似度：93%" in found

    messages = build_match_messages(found, "https://example.com/item.jpg")
    assert [message["type"] for message in messages] == ["text", "image", "text"]
    assert messages[1]["previewImageUrl"].startswith("https://")
    assert len(messages[2]["quickReply"]["items"]) == 3
