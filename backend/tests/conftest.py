import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


os.environ["DEMO_MODE"] = "true"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["MATCH_NOTIFY_THRESHOLD"] = "0.82"
os.environ["MATCH_REVIEW_THRESHOLD"] = "0.68"
os.environ["LINE_CHANNEL_SECRET"] = ""
os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = ""
os.environ["ADMIN_API_KEY"] = "test-admin-key"


@pytest.fixture(autouse=True)
def isolate_uploaded_test_images(monkeypatch: pytest.MonkeyPatch):
    """Never let API tests write generated image fixtures into project uploads/."""

    import app.services.reports as reports_service

    with TemporaryDirectory(prefix="lostlink-test-uploads-") as temporary_root:
        monkeypatch.setattr(reports_service, "PROJECT_ROOT", Path(temporary_root))
        yield
