"""Backfill text-derived category, color and campus location for existing reports."""

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal, close_database, init_database  # noqa: E402
from app.models.entities import ItemReport, MatchCandidate  # noqa: E402
from app.services.ai import MultimodalAnalyzer  # noqa: E402
from app.services.line import extract_location  # noqa: E402
from app.services.matching import score_reports  # noqa: E402


async def main() -> None:
    await init_database()
    settings = get_settings()
    analyzer = MultimodalAnalyzer(settings)
    changed = 0
    async with SessionLocal() as session:
        reports = list(await session.scalars(select(ItemReport)))
        for report in reports:
            attributes = await analyzer.analyze(report.description)
            before = (
                report.category,
                report.brand,
                report.color,
                tuple(report.distinctive_features),
                report.location,
            )
            report.category = attributes.category or report.category
            report.brand = attributes.brand or report.brand
            report.color = attributes.color or report.color
            if attributes.distinctive_features:
                report.distinctive_features = list(
                    dict.fromkeys(
                        [*report.distinctive_features, *attributes.distinctive_features]
                    )
                )
            report.location = report.location or extract_location(report.description)
            after = (
                report.category,
                report.brand,
                report.color,
                tuple(report.distinctive_features),
                report.location,
            )
            if before != after:
                changed += 1
                print(
                    f"UPDATED {report.id[:8]} category={report.category} "
                    f"color={report.color} location={report.location}",
                    flush=True,
                )
        await session.flush()
        matches = list(await session.scalars(select(MatchCandidate)))
        for match in matches:
            lost = await session.scalar(
                select(ItemReport)
                .options(selectinload(ItemReport.embedding))
                .where(ItemReport.id == match.lost_report_id)
            )
            found = await session.scalar(
                select(ItemReport)
                .options(selectinload(ItemReport.embedding))
                .where(ItemReport.id == match.found_report_id)
            )
            if not lost or not found:
                continue
            result = score_reports(
                lost,
                found,
                settings.match_notify_threshold,
                settings.match_review_threshold,
            )
            match.score = result.score
            match.decision = result.decision
            match.score_breakdown = result.breakdown
            match.reasons = result.reasons
        await session.commit()
    await close_database()
    print(f"backfilled_reports={changed} rescored_matches={len(matches)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
