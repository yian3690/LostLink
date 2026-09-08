import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import PROJECT_ROOT, get_settings
from app.core.database import SessionLocal, init_database
from app.models.entities import ItemEmbedding, ItemReport, MatchCandidate
from app.services.ai import get_embedding_service
from app.services.matching import score_reports


async def main() -> None:
    await init_database()
    settings = get_settings().model_copy(update={"demo_mode": False})
    embeddings = get_embedding_service(settings)
    async with SessionLocal() as session:
        reports = list(
            await session.scalars(
                select(ItemReport).options(
                    selectinload(ItemReport.images),
                    selectinload(ItemReport.embedding),
                )
            )
        )
        for index, report in enumerate(reports, start=1):
            image_bytes = None
            if report.images:
                image_path = (PROJECT_ROOT / report.images[0].object_path).resolve()
                uploads = (PROJECT_ROOT / "uploads").resolve()
                if uploads in image_path.parents and image_path.is_file():
                    image_bytes = image_path.read_bytes()
            record = report.embedding or ItemEmbedding(report_id=report.id)
            record.e5_text = await embeddings.encode_e5(report.description)
            record.siglip_text = await embeddings.encode_siglip_text(report.description)
            record.siglip_image = (
                await embeddings.encode_siglip_image(image_bytes) if image_bytes else None
            )
            record.e5_model = settings.e5_model
            record.siglip_model = settings.siglip_model
            if report.embedding is None:
                session.add(record)
                report.embedding = record
            print(f"embedded={index}/{len(reports)}", flush=True)
        await session.flush()

        existing = {
            (item.lost_report_id, item.found_report_id): item
            for item in list(await session.scalars(select(MatchCandidate)))
        }
        lost_reports = [item for item in reports if item.kind == "lost" and item.status == "open"]
        found_reports = [item for item in reports if item.kind == "found" and item.status == "open"]
        for lost in lost_reports:
            for found in found_reports:
                result = score_reports(
                    lost,
                    found,
                    settings.match_notify_threshold,
                    settings.match_review_threshold,
                )
                match = existing.get((lost.id, found.id))
                if result.score < 0.40 and match is None:
                    continue
                if match is None:
                    match = MatchCandidate(
                        lost_report_id=lost.id,
                        found_report_id=found.id,
                        score=result.score,
                    )
                    session.add(match)
                match.score = result.score
                match.decision = result.decision
                match.score_breakdown = result.breakdown
                match.reasons = result.reasons
        await session.commit()
        print(f"reindexed_reports={len(reports)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
