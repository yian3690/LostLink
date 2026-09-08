import base64
import io

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


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

        deleted = client.delete(f"/api/v1/admin/reports/{report_id}", headers=headers)
        assert deleted.status_code == 204

        reports = client.get("/api/v1/admin/reports", headers=headers)
        assert reports.status_code == 200
        assert all(item["id"] != report_id for item in reports.json())
