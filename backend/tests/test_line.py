import base64
import hashlib
import hmac
from types import SimpleNamespace

from app.core.config import Settings
from app.services.matching import location_score
from app.services.line import (
    LineClient,
    build_match_messages,
    build_match_reply,
    extract_location,
    extract_time_hint,
    parse_report_kind,
    time_hint_matches,
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
    assert extract_location("我的耳機在圖書館三樓不見了") == "圖書館 3F"
    assert extract_location("紫色錢包，牛皮的，在走廊撿到") == "走廊"
    assert extract_location("我在二樓樓梯間撿到雨傘") == "2F 樓梯間"
    assert extract_location("我今天在學餐有東西不見") == "學生餐廳"
    assert extract_location("我在三樓有東西不見") == "3F"
    assert location_score("學餐", "學生餐廳 1F 靠窗座位") > 0
    assert location_score("三樓", "教學大樓 3F 服務台") > 0
    assert location_score("三樓", "綜合大樓 301 教室") > 0
    assert location_score("三樓", "學生餐廳 1F 靠窗座位") == 0


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


def test_extract_time_hint_understands_common_chinese_time() -> None:
    datetime_module = __import__("datetime")
    now = datetime_module.datetime(
        2026,
        9,
        11,
        13,
        30,
        tzinfo=datetime_module.timezone(datetime_module.timedelta(hours=8)),
    )
    yesterday, uncertainty = extract_time_hint("我昨天下午在圖書館掉了飲料", now)
    assert yesterday.strftime("%Y-%m-%d %H:%M") == "2026-09-10 15:00"
    assert uncertainty == 4.0
    exact, uncertainty = extract_time_hint("9/7 下午6點在教室", now)
    assert exact.strftime("%Y-%m-%d %H:%M") == "2026-09-07 18:00"
    assert uncertainty == 1.0
    current, uncertainty = extract_time_hint("這是現在撿到的", now)
    assert current == now
    assert uncertainty == 3.0


def test_date_only_time_hint_matches_only_that_calendar_day() -> None:
    datetime_module = __import__("datetime")
    taipei = datetime_module.timezone(datetime_module.timedelta(hours=8))
    center, uncertainty = extract_time_hint(
        "我的雨傘在 9/10 不見了",
        datetime_module.datetime(2026, 9, 11, 16, 0, tzinfo=taipei),
    )

    assert time_hint_matches(
        datetime_module.datetime(2026, 9, 10, 16, 40, tzinfo=taipei),
        center,
        uncertainty,
    )
    assert not time_hint_matches(
        datetime_module.datetime(2026, 9, 9, 16, 40, tzinfo=taipei),
        center,
        uncertainty,
    )
