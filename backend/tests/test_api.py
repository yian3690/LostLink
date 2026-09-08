import base64
import io

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.api.line_webhook import _is_list_request
from app.services.line import LineClient
from app.services.ollama import OllamaService


def test_natural_found_item_questions_are_database_searches() -> None:
    assert _is_list_request("有水壺嗎")
    assert _is_list_request("目前有人撿到錢包嗎")
    assert _is_list_request("請問有撿到水杯嗎？")
    assert not _is_list_request("你好嗎")


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


def test_line_image_without_intent_is_not_saved(monkeypatch) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 60), color=(40, 180, 80)).save(buffer, "PNG")
    image_bytes = buffer.getvalue()

    async def fake_download(self, message_id: str) -> bytes:
        return image_bytes

    monkeypatch.setattr(LineClient, "download_content", fake_download)
    with TestClient(app) as client:
        before = client.get("/api/v1/reports").json()
        response = client.post(
            "/webhooks/line",
            json={
                "events": [
                    {
                        "type": "message",
                        "replyToken": "test-reply-token",
                        "source": {"type": "user", "userId": "photo-only-user"},
                        "message": {"type": "image", "id": "test-image"},
                    }
                ]
            },
        )
        after = client.get("/api/v1/reports").json()

    assert response.status_code == 200
    assert len(after) == len(before)


def test_line_combines_location_then_item_into_one_report(monkeypatch) -> None:
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
        after = client.get("/api/v1/reports").json()

    assert first.status_code == 200
    assert second.status_code == 200
    assert unselected.status_code == 200
    assert selected.status_code == 200
    assert len(after_unselected) == len(before)
    assert len(middle) == len(before)
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
