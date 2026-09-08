"""Re-run local multimodal analysis for photo reports with incomplete AI fields."""

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal, close_database, init_database  # noqa: E402
from app.models.entities import ItemEmbedding, ItemReport  # noqa: E402
from app.services.ai import MultimodalAnalyzer, get_embedding_service  # noqa: E402


GENERIC_DESCRIPTIONS = {
    "",
    "拾獲者上傳的物品照片",
    "使用者上傳的拾獲物品照片",
    "使用者上傳的遺失物照片",
}


async def main() -> None:
    await init_database()
    settings = get_settings()
    analyzer = MultimodalAnalyzer(settings)
    embeddings = get_embedding_service(settings)
    changed = 0

    async with SessionLocal() as session:
        reports = list(
            await session.scalars(
                select(ItemReport).options(
                    selectinload(ItemReport.images),
                    selectinload(ItemReport.embedding),
                )
            )
        )
        for report in reports:
            incomplete = (
                not report.category
                or not report.color
                or not report.distinctive_features
                or report.description.strip() in GENERIC_DESCRIPTIONS
            )
            if not incomplete or not report.images:
                continue

            image_path = (PROJECT_ROOT / report.images[0].object_path).resolve()
            uploads_root = (PROJECT_ROOT / "uploads").resolve()
            if uploads_root not in image_path.parents or not image_path.is_file():
                print(f"SKIP missing-image {report.id}", flush=True)
                continue

            image_bytes = image_path.read_bytes()
            attributes = await analyzer.analyze(report.description, image_bytes)
            report.description = attributes.normalized_description or report.description
            report.category = attributes.category or report.category
            report.brand = attributes.brand or report.brand
            report.color = attributes.color or report.color
            report.distinctive_features = attributes.distinctive_features

            record = report.embedding or ItemEmbedding(report_id=report.id)
            record.e5_text = await embeddings.encode_e5(report.description)
            record.siglip_text = await embeddings.encode_siglip_text(report.description)
            if not record.siglip_image:
                record.siglip_image = await embeddings.encode_siglip_image(image_bytes)
            record.e5_model = settings.e5_model
            record.siglip_model = settings.siglip_model
            if report.embedding is None:
                session.add(record)
                report.embedding = record

            changed += 1
            print(
                f"UPDATED {report.id[:8]} category={report.category} "
                f"color={report.color} features={report.distinctive_features}",
                flush=True,
            )

        await session.commit()

    await close_database()
    print(f"reanalyzed_reports={changed}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
