from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.schemas.reports import (
    DashboardStats,
    MatchRead,
    ReportCreate,
    ReportCreated,
    ReportRead,
)
from app.services.reports import ReportService


router = APIRouter(prefix="/api/v1", tags=["reports"])


def service_dependency(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ReportService:
    return ReportService(session, settings)


def to_report_read(report) -> ReportRead:
    item = ReportRead.model_validate(report)
    if getattr(report, "images", None):
        item.image_url = f"/api/v1/reports/{report.id}/image"
    return item


@router.post("/reports", response_model=ReportCreated, status_code=201)
async def create_report(
    payload: ReportCreate,
    service: ReportService = Depends(service_dependency),
) -> ReportCreated:
    if not payload.description.strip() and not payload.image_base64:
        raise HTTPException(422, "description or image_base64 is required")
    try:
        report, matches = await service.create(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return ReportCreated(
        report=to_report_read(report),
        matches=[MatchRead.model_validate(match) for match in matches],
    )


@router.get("/reports", response_model=list[ReportRead])
async def list_reports(
    limit: int = Query(50, ge=1, le=200),
    kind: Literal["lost", "found"] | None = Query(default=None),
    service: ReportService = Depends(service_dependency),
) -> list[ReportRead]:
    reports = await service.list_reports(limit, kind)
    return [to_report_read(report) for report in reports]


@router.get("/reports/{report_id}/image", response_class=FileResponse)
async def public_report_image(
    report_id: str,
    service: ReportService = Depends(service_dependency),
) -> FileResponse:
    thumbnail = await service.public_thumbnail(report_id)
    if not thumbnail:
        raise HTTPException(404, "Public thumbnail not found")
    return FileResponse(
        thumbnail,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/reports/{report_id}/matches", response_model=list[MatchRead])
async def list_matches(
    report_id: str,
    service: ReportService = Depends(service_dependency),
) -> list[MatchRead]:
    matches = await service.list_matches(report_id)
    return [MatchRead.model_validate(match) for match in matches]


@router.get("/dashboard/stats", response_model=DashboardStats)
async def dashboard_stats(
    service: ReportService = Depends(service_dependency),
) -> DashboardStats:
    return await service.stats()


@router.post("/demo/seed", response_model=list[ReportCreated])
async def seed_demo(
    service: ReportService = Depends(service_dependency),
    settings: Settings = Depends(get_settings),
) -> list[ReportCreated]:
    if not settings.demo_mode:
        raise HTTPException(404, "Demo endpoints are disabled")
    now = datetime.now(timezone.utc)
    lost, lost_matches = await service.create(
        ReportCreate(
            kind="lost",
            description="我的黑色 AirPods Pro 在圖書館三樓不見了，有黑色保護殼",
            campus="示範大學",
            location="圖書館 3F",
            occurred_at=now - timedelta(minutes=30),
            line_user_id="demo-lost-user",
        )
    )
    found, found_matches = await service.create(
        ReportCreate(
            kind="found",
            description="撿到一副黑色 Apple 無線耳機，有黑色保護殼",
            campus="示範大學",
            location="圖書館 2F",
            occurred_at=now - timedelta(minutes=8),
            line_user_id="demo-found-user",
        )
    )
    return [
        ReportCreated(
            report=to_report_read(lost),
            matches=[MatchRead.model_validate(item) for item in lost_matches],
        ),
        ReportCreated(
            report=to_report_read(found),
            matches=[MatchRead.model_validate(item) for item in found_matches],
        ),
    ]
