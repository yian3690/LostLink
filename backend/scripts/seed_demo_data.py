"""Insert a small, repeatable campus lost-and-found demo dataset."""

import asyncio
import base64
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal, init_database  # noqa: E402
from app.models.entities import ItemReport  # noqa: E402
from app.schemas.reports import ReportCreate  # noqa: E402
from app.services.reports import ReportService  # noqa: E402


NOW = datetime.now(timezone(timedelta(hours=8)))
DEMO_ITEMS = [
    {
        "description": "[DEMO] 透明寶特瓶裝琥珀色茶飲，綠色瓶蓋與白綠色標籤",
        "location": "圖書館 2F 自習區",
        "occurred_at": NOW - timedelta(minutes=35),
        "image": "found-bottled-tea.png",
    },
    {
        "description": "[DEMO] 黑色真無線藍牙耳機與黑色充電盒",
        "location": "圖書館 3F 靠窗座位",
        "occurred_at": NOW - timedelta(minutes=55),
        "image": "found-black-earbuds.png",
    },
    {
        "description": "[DEMO] 深藍色折疊雨傘，黑色彎把",
        "location": "教學大樓 2F 走廊",
        "occurred_at": NOW - timedelta(hours=2),
        "image": "found-blue-umbrella.png",
    },
    {
        "description": "[DEMO] 黑色不鏽鋼保溫瓶，瓶身有白色方形貼紙",
        "location": "綜合大樓 301 教室",
        "occurred_at": NOW - timedelta(hours=3),
        "image": "found-black-bottle.png",
    },
]


async def main() -> None:
    await init_database()
    settings = get_settings().model_copy(update={"line_channel_access_token": ""})
    created: list[ItemReport] = []
    async with SessionLocal() as session:
        service = ReportService(session, settings)
        for item in DEMO_ITEMS:
            existing = await session.scalar(
                select(ItemReport).where(ItemReport.description == item["description"])
            )
            if existing:
                print(f"SKIP {existing.id} {existing.description}")
                continue
            image_bytes = (PROJECT_ROOT / "demo-assets" / item["image"]).read_bytes()
            report, _ = await service.create(
                ReportCreate(
                    kind="found",
                    description=item["description"],
                    campus="LostLink 示範大學",
                    location=item["location"],
                    occurred_at=item["occurred_at"],
                    image_base64=base64.b64encode(image_bytes).decode("ascii"),
                )
            )
            created.append(report)
            print(f"CREATE {report.id} {report.description}")
    print(f"Demo seed complete: {len(created)} created.")


if __name__ == "__main__":
    asyncio.run(main())
