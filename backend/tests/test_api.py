import base64
import io

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.api.line_webhook import (
    _is_image_search_request,
    _is_list_request,
    _is_tracking_request,
)
from app.services.line import LineClient
from app.services.ollama import OllamaService


def test_natural_found_item_questions_are_database_searches() -> None:
    assert _is_list_request("有水壺嗎")
    assert _is_list_request("目前有人撿到錢包嗎")
    assert _is_list_request("請問有撿到水杯嗎？")
    assert not _is_list_request("你好嗎")
    assert _is_image_search_request("你有看到類似的嗎")
    assert _is_image_search_request("幫我找像這張的物品")
    assert _is_list_request("你有看到圖書館2樓的飲料嗎")
    assert _is_tracking_request("幫我建立持續搜尋")
    assert _is_tracking_request("我要持續協尋")


def test_generic_item_query_does_not_invent_a_category() -> None:
    from app.services.reports import ReportService

    assert ReportService._is_generic_item_query("我在圖書館有東西不見")
    assert not ReportService._is_generic_item_query("我在圖書館有飲料不見")
    assert not ReportService._is_generic_item_query(
        "我在三樓有東西不見，後來想找黑色保溫杯"
    )


def test_demo_flow_creates_candidate() -> None:
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["demo_mode"] is True

        lost = client.post(
            "/api/v1/reports",
            json={
                "kind": "lost",
                "description": "黑色 AirPods 在圖書館不見了",
                "campus": "示範大學",
                "location": "圖書館",
                "line_user_id": "student-a",
            },
        )
        assert lost.status_code == 201

        found = client.post(
            "/api/v1/reports",
            json={
                "kind": "found",
                "description": "圖書館撿到黑色 Apple 無線耳機",
                "campus": "示範大學",
                "location": "圖書館",
                "line_user_id": "student-b",
            },
        )
        assert found.status_code == 201
        matches = found.json()["matches"]
        assert len(matches) == 1
        assert matches[0]["score"] >= 0.4

        claim = client.post(
            f"/api/v1/matches/{matches[0]['id']}/claims",
            json={
                "line_user_id": "student-a",
                "private_evidence": "保護殼內側有 LL 刻字",
            },
        )
        assert claim.status_code == 201
        review = client.post(
            f"/api/v1/claims/{claim.json()['id']}/review",
            headers={"X-Admin-Key": "test-admin-key"},
            json={
                "approved": True,
                "reviewer_line_user_id": "campus-admin",
            },
        )
        assert review.status_code == 200
        assert review.json()["status"] == "approved"

        stats = client.get("/api/v1/dashboard/stats")
        assert stats.status_code == 200
        assert stats.json()["open_lost"] == 0
        assert stats.json()["open_found"] == 0
        assert stats.json()["returned_items"] == 1


def test_empty_report_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/reports",
            json={"kind": "lost", "description": ""},
        )
        assert response.status_code == 422


def test_report_extracts_classroom_location() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/reports",
            json={
                "kind": "lost",
                "description": "我的黃色飲料在 ZB302 教室不見了",
            },
        )
        assert response.status_code == 201
        report = response.json()["report"]
        assert report["color"] == "yellow"
        assert report["location"] == "ZB302教室"


def test_repeated_open_report_from_same_user_is_deduplicated() -> None:
    payload = {
        "kind": "lost",
        "description": "我的黑色耳機在 A101 教室不見了",
        "line_user_id": "duplicate-report-user",
    }
    with TestClient(app) as client:
        first = client.post("/api/v1/reports", json=payload)
        second = client.post("/api/v1/reports", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["report"]["id"] == second.json()["report"]["id"]


def test_found_photo_has_safe_public_thumbnail_and_kind_filter() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 60), color=(20, 20, 20)).save(buffer, "PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    with TestClient(app) as client:
        found = client.post(
            "/api/v1/reports",
            json={
                "kind": "found",
                "description": "黑色耳機",
                "location": "圖書館 2F",
                "image_base64": f"data:image/png;base64,{encoded}",
            },
        )
        assert found.status_code == 201
        report = found.json()["report"]
        assert report["image_url"].endswith("/image")
        assert report["category"] == "earphones"
        assert report["color"] == "black"
        assert report["distinctive_features"]

        thumbnail = client.get(report["image_url"])
        assert thumbnail.status_code == 200
        assert thumbnail.headers["content-type"] == "image/jpeg"
        with Image.open(io.BytesIO(thumbnail.content)) as image:
            assert image.format == "JPEG"
            assert image.width <= 720
            assert image.height <= 720

        found_only = client.get("/api/v1/reports?kind=found")
        assert found_only.status_code == 200
        assert found_only.json()
        assert all(item["kind"] == "found" for item in found_only.json())


def test_lost_photo_is_visible_only_to_verified_owner_or_admin(monkeypatch) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 60), color=(35, 40, 45)).save(buffer, "PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    async def fake_verify(self, access_token: str) -> str | None:
        return "verified-owner" if access_token == "valid-owner-token" else None

    monkeypatch.setattr(LineClient, "verify_access_token", fake_verify)

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/reports",
            json={
                "kind": "lost",
                "description": "黑色水壺",
                "line_user_id": "verified-owner",
                "image_base64": encoded,
            },
        )
        assert created.status_code == 201
        report = created.json()["report"]

        assert client.get(report["image_url"]).status_code == 404
        assert client.get("/api/v1/reports/mine").status_code == 401
        mine = client.get(
            "/api/v1/reports/mine",
            headers={"Authorization": "Bearer valid-owner-token"},
        )
        assert mine.status_code == 200
        assert any(item["id"] == report["id"] for item in mine.json())
        updated = client.patch(
            f"/api/v1/reports/{report['id']}/mine",
            headers={"Authorization": "Bearer valid-owner-token"},
            json={
                "description": "黑色 700ml 水壺，瓶蓋有按鈕",
                "location": "ZB301 教室",
                "occurred_at": "2026-09-12T10:00:00+08:00",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["report"]["location"] == "ZB301 教室"
        assert updated.json()["report"]["occurred_at"].startswith("2026-09-12T10:00:00")

        manual = client.patch(
            f"/api/v1/admin/reports/{report['id']}",
            headers={"X-Admin-Key": "test-admin-key"},
            json={
                "description": "黑色 700ml 運動水壺",
                "location": "ZB301 教室",
                "occurred_at": "2026-09-12T10:00:00+08:00",
                "category": "bottle",
                "brand": "Guide To Life",
                "color": "black",
                "distinctive_features": ["瓶蓋有按鈕", "瓶身有刻度"],
            },
        )
        assert manual.status_code == 200
        assert manual.json()["report"]["color"] == "black"
        assert manual.json()["report"]["brand"] == "Guide To Life"
        assert manual.json()["report"]["distinctive_features"] == ["瓶蓋有按鈕", "瓶身有刻度"]
        owner_image = client.get(
            f"/api/v1/reports/{report['id']}/owner-image",
            headers={"Authorization": "Bearer valid-owner-token"},
        )
        assert owner_image.status_code == 200
        admin_image = client.get(
            f"/api/v1/admin/reports/{report['id']}/image",
            headers={"X-Admin-Key": "test-admin-key"},
        )
        assert admin_image.status_code == 200


def test_invalid_image_is_rejected() -> None:
    encoded = base64.b64encode(b"not-an-image").decode("ascii")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/reports",
            json={
                "kind": "found",
                "description": "測試",
                "image_base64": encoded,
            },
        )
        assert response.status_code == 422


def test_owner_can_resolve_search_with_matched_found_item(monkeypatch) -> None:
    async def fake_verify(self, access_token: str) -> str | None:
        return "resolve-owner" if access_token == "resolve-owner-token" else None

    monkeypatch.setattr(LineClient, "verify_access_token", fake_verify)
    headers = {"Authorization": "Bearer resolve-owner-token"}
    with TestClient(app) as client:
        lost = client.post(
            "/api/v1/reports",
            json={
                "kind": "lost",
                "description": "黑色長柄雨傘，木質握把，傘緣有灰色反光條",
                "location": "圖書館一樓入口",
                "line_user_id": "resolve-owner",
            },
        ).json()["report"]
        found_response = client.post(
            "/api/v1/reports",
            json={
                "kind": "found",
                "description": "黑色長柄雨傘，木質握把，傘緣有灰色反光條",
                "location": "圖書館一樓入口",
                "line_user_id": "resolve-finder",
            },
        )
        found = found_response.json()["report"]
        assert any(
            item["lost_report_id"] == lost["id"]
            for item in found_response.json()["matches"]
        )

        unresolved = client.post(
            f"/api/v1/reports/{lost['id']}/mine/resolve",
            json={"found_report_id": found["id"]},
        )
        assert unresolved.status_code == 401
        resolved = client.post(
            f"/api/v1/reports/{lost['id']}/mine/resolve",
            headers=headers,
            json={"found_report_id": found["id"]},
        )
        assert resolved.status_code == 200
        assert resolved.json()["lost_report"]["status"] == "returned"
        assert resolved.json()["found_report"]["status"] == "returned"

        reports = client.get("/api/v1/reports?limit=200").json()
        statuses = {item["id"]: item["status"] for item in reports}
        assert statuses[lost["id"]] == "returned"
        assert statuses[found["id"]] == "returned"


def test_line_image_asks_intent_before_searching_or_saving(monkeypatch) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 60), color=(40, 180, 80)).save(buffer, "PNG")
    image_bytes = buffer.getvalue()
    replies = []
    searches = []

    async def fake_download(self, message_id: str) -> bytes:
        return image_bytes

    async def fake_reply(self, reply_token: str, message) -> None:
        replies.append(message)

    async def fake_search(self, description, image_bytes=None, location=None, limit=3):
        searches.append((description, image_bytes))
        return []

    monkeypatch.setattr(LineClient, "download_content", fake_download)
    monkeypatch.setattr(LineClient, "reply", fake_reply)
    monkeypatch.setattr("app.services.reports.ReportService.search_found", fake_search)
    user_id = "photo-intent-user"

    def postback(action: str) -> dict:
        return {
            "events": [{
                "type": "postback",
                "replyToken": "test-reply-token",
                "source": {"type": "user", "userId": user_id},
                "postback": {"data": action},
            }]
        }

    with TestClient(app) as client:
        before = client.get("/api/v1/reports").json()
        response = client.post(
            "/webhooks/line",
            json={
                "events": [{
                    "type": "message",
                    "replyToken": "test-reply-token",
                    "source": {"type": "user", "userId": user_id},
                    "message": {"type": "image", "id": "test-image"},
                }]
            },
        )
        after_image = client.get("/api/v1/reports").json()
        search_response = client.post(
            "/webhooks/line", json=postback("action=photo_lost")
        )
        searched = client.post(
            "/webhooks/line", json=postback("action=search_photo")
        )
        after_search = client.get("/api/v1/reports").json()

    assert response.status_code == 200
    assert search_response.status_code == 200
    assert searched.status_code == 200
    assert len(after_image) == len(before)
    assert len(after_search) == len(before)
    assert len(searches) == 1
    actions = replies[0][0]["quickReply"]["items"]
    assert {item["action"]["data"] for item in actions} == {
        "action=photo_found",
        "action=photo_lost",
        "action=continue_chat",
    }
    lost_actions = replies[1][0]["quickReply"]["items"]
    assert {item["action"]["data"] for item in lost_actions} == {
        "action=search_photo",
        "action=enable_tracking",
        "action=continue_chat",
    }


def test_line_combines_clues_and_requires_tracking_confirmation(monkeypatch) -> None:
    async def fake_clarification(self, kind, user_text, previous_context, has_image):
        return "收到地點了，請再告訴我物品名稱或上傳照片。"

    monkeypatch.setattr(OllamaService, "clarification", fake_clarification)

    def event(text: str) -> dict:
        return {
            "events": [
                {
                    "type": "message",
                    "replyToken": "test-reply-token",
                    "source": {"type": "user", "userId": "location-first-user"},
                    "message": {"type": "text", "text": text},
                }
            ]
        }

    with TestClient(app) as client:
        before = client.get("/api/v1/reports").json()
        unselected = client.post(
            "/webhooks/line",
            json=event("我的黃色飲料不見了"),
        )
        after_unselected = client.get("/api/v1/reports").json()
        selected = client.post(
            "/webhooks/line",
            json={
                "events": [
                    {
                        "type": "postback",
                        "replyToken": "test-reply-token",
                        "source": {"type": "user", "userId": "location-first-user"},
                        "postback": {"data": "action=start_lost"},
                    }
                ]
            },
        )
        first = client.post(
            "/webhooks/line",
            json=event("我在 ZB302 教室有東西不見"),
        )
        middle = client.get("/api/v1/reports").json()
        second = client.post(
            "/webhooks/line",
            json=event("我的黃色飲料不見了"),
        )
        before_confirmation = client.get("/api/v1/reports").json()
        confirmed = client.post(
            "/webhooks/line",
            json={
                "events": [
                    {
                        "type": "postback",
                        "replyToken": "test-reply-token",
                        "source": {"type": "user", "userId": "location-first-user"},
                        "postback": {"data": "action=enable_tracking"},
                    }
                ]
                },
            )
        supplied_time = client.post(
            "/webhooks/line",
            json=event("遺失時間大概早上 10 點多"),
        )
        created = client.post(
            "/webhooks/line",
            json={
                "events": [
                    {
                        "type": "postback",
                        "replyToken": "test-reply-token",
                        "source": {"type": "user", "userId": "location-first-user"},
                        "postback": {"data": "action=confirm_tracking"},
                    }
                ]
            },
        )
        after = client.get("/api/v1/reports").json()

    assert first.status_code == 200
    assert second.status_code == 200
    assert unselected.status_code == 200
    assert selected.status_code == 200
    assert confirmed.status_code == 200
    assert supplied_time.status_code == 200
    assert created.status_code == 200
    assert len(after_unselected) == len(before)
    assert len(middle) == len(before)
    assert len(before_confirmation) == len(before)
    assert len(after) == len(before) + 1
    assert after[0]["location"] == "ZB302教室"
    assert after[0]["color"] == "yellow"


def test_line_general_chat_mode_never_creates_report(monkeypatch) -> None:
    async def fake_general_chat(self, user_text, history, official_url):
        return "現在是一般聊天模式；要協尋錢包，請先點選下方的「我遺失物品」。"

    monkeypatch.setattr(OllamaService, "general_chat", fake_general_chat)
    user_id = "general-chat-user"
    with TestClient(app) as client:
        before = client.get("/api/v1/reports").json()
        selected = client.post(
            "/webhooks/line",
            json={
                "events": [
                    {
                        "type": "postback",
                        "replyToken": "test-reply-token",
                        "source": {"type": "user", "userId": user_id},
                        "postback": {"data": "action=continue_chat"},
                    }
                ]
            },
        )
        chatted = client.post(
            "/webhooks/line",
            json={
                "events": [
                    {
                        "type": "message",
                        "replyToken": "test-reply-token",
                        "source": {"type": "user", "userId": user_id},
                        "message": {"type": "text", "text": "我的錢包不見了"},
                    }
                ]
            },
        )
        after = client.get("/api/v1/reports").json()

    assert selected.status_code == 200
    assert chatted.status_code == 200
    assert len(after) == len(before)


def test_line_search_follow_up_combines_item_location_and_date(monkeypatch) -> None:
    searches = []

    async def fake_search(self, description, image_bytes=None, location=None, limit=3):
        searches.append((description, location))
        return []

    monkeypatch.setattr("app.services.reports.ReportService.search_found", fake_search)
    user_id = "combined-search-context-user"

    def event(text: str) -> dict:
        return {
            "events": [
                {
                    "type": "message",
                    "replyToken": "test-reply-token",
                    "source": {"type": "user", "userId": user_id},
                    "message": {"type": "text", "text": text},
                }
            ]
        }

    with TestClient(app) as client:
        client.post("/webhooks/line", json=event("我的雨傘不見了"))
        client.post("/webhooks/line", json=event("是在綜合大樓不見的"))
        client.post("/webhooks/line", json=event("是 9/10 不見的"))

    description, location = searches[-1]
    assert "雨傘" in description
    assert "綜合大樓" in description
    assert "9/10" in description
    assert location == "綜合大樓"

    searches.clear()
    user_id = "latest-location-wins-user"
    with TestClient(app) as client:
        client.post("/webhooks/line", json=event("我今天在學生餐廳有東西不見"))
        client.post("/webhooks/line", json=event("我在三樓有東西不見"))

    description, location = searches[-1]
    assert "學生餐廳" in description
    assert "三樓" in description
    assert location == "3F"

    searches.clear()
    user_id = "complete-search-resets-context-user"
    with TestClient(app) as client:
        client.post("/webhooks/line", json=event("我在三樓有東西不見"))
        client.post("/webhooks/line", json=event("你有看到黑色保溫杯嗎"))

    description, location = searches[-1]
    assert description == "你有看到黑色保溫杯嗎"
    assert location is None


def test_found_registration_keeps_photo_location_features_and_confirms_time(monkeypatch) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 60), color=(85, 45, 105)).save(buffer, "PNG")
    replies = []

    async def fake_download(self, message_id: str) -> bytes:
        return buffer.getvalue()

    async def fake_reply(self, reply_token: str, message) -> None:
        replies.append(message)

    monkeypatch.setattr(LineClient, "download_content", fake_download)
    monkeypatch.setattr(LineClient, "reply", fake_reply)
    user_id = "found-time-confirm-user"

    def postback(action: str) -> dict:
        return {
            "events": [
                {
                    "type": "postback",
                    "replyToken": "test-reply-token",
                    "source": {"type": "user", "userId": user_id},
                    "postback": {"data": action},
                }
            ]
        }

    def message(message_type: str, **values) -> dict:
        return {
            "events": [
                {
                    "type": "message",
                    "replyToken": "test-reply-token",
                    "source": {"type": "user", "userId": user_id},
                    "message": {"type": message_type, **values},
                }
            ]
        }

    with TestClient(app) as client:
        before = client.get("/api/v1/reports").json()
        client.post("/webhooks/line", json=postback("action=start_found"))
        client.post(
            "/webhooks/line", json=message("image", id="wallet-photo")
        )
        client.post(
            "/webhooks/line",
            json=message("text", text="紫色錢包，牛皮的，在走廊撿到"),
        )
        before_confirmation = client.get("/api/v1/reports").json()
        client.post(
            "/webhooks/line", json=postback("action=confirm_found_now")
        )
        after = client.get("/api/v1/reports").json()

    assert len(before_confirmation) == len(before)
    assert len(after) == len(before) + 1
    created = after[0]
    assert created["location"] == "走廊"
    assert created["occurred_at"] is not None
    assert "牛皮材質" in created["distinctive_features"]
    assert "感謝提供資訊" in replies[-1][0]["text"]
    assert len(replies[-1]) == 1


def test_local_database_admin_crud() -> None:
    with TestClient(app) as client:
        headers = {"X-Admin-Key": "test-admin-key"}
        created = client.post(
            "/api/v1/admin/reports",
            headers=headers,
            json={
                "kind": "found",
                "description": "資料庫管理測試雨傘",
                "location": "測試走廊",
            },
        )
        assert created.status_code == 201
        report_id = created.json()["report"]["id"]

        updated = client.patch(
            f"/api/v1/admin/reports/{report_id}",
            headers=headers,
            json={"description": "資料庫管理測試藍色雨傘"},
        )
        assert updated.status_code == 200
        assert updated.json()["report"]["description"] == "資料庫管理測試藍色雨傘"

        status = client.patch(
            f"/api/v1/admin/reports/{report_id}/status",
            headers=headers,
            json={"status": "returned"},
        )
        assert status.status_code == 200
        assert status.json()["status"] == "returned"

        deleted = client.delete(f"/api/v1/admin/reports/{report_id}", headers=headers)
        assert deleted.status_code == 204

        reports = client.get("/api/v1/admin/reports", headers=headers)
        assert reports.status_code == 200
        assert all(item["id"] != report_id for item in reports.json())
