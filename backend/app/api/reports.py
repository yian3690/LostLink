from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.schemas.reports import (
    DashboardStats,
    MatchRead,
    OwnerReportResolve,
    OwnerReportResolved,
    OwnerReportUpdate,
    ReportCreate,
    ReportCreated,
    ReportRead,
)
from app.services.reports import ReportService
from app.services.line import LineClient


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


async def verified_line_user(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "LINE access token is required")
    line_user_id = await LineClient(settings).verify_access_token(
        authorization.split(" ", 1)[1].strip()
    )
    if not line_user_id:
        raise HTTPException(401, "Invalid LINE access token")
    return line_user_id


@router.get("/reports/mine", response_model=list[ReportRead])
async def list_my_reports(
    limit: int = Query(50, ge=1, le=100),
    kind: Literal["lost", "found"] | None = Query(default="lost"),
    line_user_id: str = Depends(verified_line_user),
    service: ReportService = Depends(service_dependency),
) -> list[ReportRead]:
    reports = await service.list_user_reports(line_user_id, limit, kind)
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


@router.get("/reports/{report_id}/owner-image", response_class=FileResponse)
async def owner_report_image(
    report_id: str,
    line_user_id: str = Depends(verified_line_user),
    service: ReportService = Depends(service_dependency),
) -> FileResponse:
    thumbnail = await service.owner_thumbnail(report_id, line_user_id)
    if not thumbnail:
        raise HTTPException(404, "Owner thumbnail not found")
    return FileResponse(
        thumbnail,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.patch("/reports/{report_id}/mine", response_model=ReportCreated)
async def update_my_report(
    report_id: str,
    payload: OwnerReportUpdate,
    line_user_id: str = Depends(verified_line_user),
    service: ReportService = Depends(service_dependency),
) -> ReportCreated:
    result = await service.update_owned_report(report_id, line_user_id, payload)
    if not result:
        raise HTTPException(404, "Owned lost report not found")
    report, matches = result
    return ReportCreated(
        report=to_report_read(report),
        matches=[MatchRead.model_validate(match) for match in matches],
    )


@router.post("/reports/{report_id}/mine/resolve", response_model=OwnerReportResolved)
async def resolve_my_report(
    report_id: str,
    payload: OwnerReportResolve,
    line_user_id: str = Depends(verified_line_user),
    service: ReportService = Depends(service_dependency),
) -> OwnerReportResolved:
    try:
        result = await service.resolve_owned_report(
            report_id, line_user_id, payload.found_report_id
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not result:
        raise HTTPException(404, "Owned lost report not found")
    lost, found = result
    # Closing the database report also ends its in-memory LINE workflow.
    # Otherwise an unrelated photo may inherit the resolved item's clues.
    from app.api.line_webhook import clear_user_conversation_context

    clear_user_conversation_context(line_user_id)
    return OwnerReportResolved(
        lost_report=to_report_read(lost),
        found_report=to_report_read(found) if found else None,
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
