import os


os.environ["DEMO_MODE"] = "true"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["MATCH_NOTIFY_THRESHOLD"] = "0.82"
os.environ["MATCH_REVIEW_THRESHOLD"] = "0.68"
os.environ["LINE_CHANNEL_SECRET"] = ""
os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = ""
os.environ["ADMIN_API_KEY"] = "test-admin-key"
