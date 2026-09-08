import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from app.api.reports import service_dependency, to_report_read
from app.core.config import Settings, get_settings
from app.schemas.reports import (
    ReportCreate,
    ReportCreated,
    ReportDescriptionUpdate,
    ReportStatusUpdate,
    ReportRead,
    MatchRead,
)
from app.services.reports import ReportService


router = APIRouter(prefix="/api/v1/admin", tags=["database-admin"])


def require_database_admin(
    x_admin_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.admin_api_key and x_admin_key and hmac.compare_digest(
        settings.admin_api_key, x_admin_key
    ):
        return
    raise HTTPException(403, "Invalid database administration key")


@router.get(
    "/reports",
    response_model=list[ReportRead],
    dependencies=[Depends(require_database_admin)],
)
async def admin_list_reports(
    limit: int = Query(200, ge=1, le=500),
    service: ReportService = Depends(service_dependency),
) -> list[ReportRead]:
    return [to_report_read(item) for item in await service.list_reports(limit)]


@router.post(
    "/reports",
    response_model=ReportCreated,
    status_code=201,
    dependencies=[Depends(require_database_admin)],
)
async def admin_create_report(
    payload: ReportCreate,
    service: ReportService = Depends(service_dependency),
) -> ReportCreated:
    if not payload.description.strip() and not payload.image_base64:
        raise HTTPException(422, "description or image_base64 is required")
    report, matches = await service.create(payload)
    return ReportCreated(
        report=to_report_read(report),
        matches=[MatchRead.model_validate(item) for item in matches],
    )


@router.patch(
    "/reports/{report_id}",
    response_model=ReportCreated,
    dependencies=[Depends(require_database_admin)],
)
async def admin_update_report(
    report_id: str,
    payload: ReportDescriptionUpdate,
    service: ReportService = Depends(service_dependency),
) -> ReportCreated:
    result = await service.replace_description(report_id, payload.description)
    if not result:
        raise HTTPException(404, "Report not found")
    report, matches = result
    return ReportCreated(
        report=to_report_read(report),
        matches=[MatchRead.model_validate(item) for item in matches],
    )


@router.patch(
    "/reports/{report_id}/status",
    response_model=ReportRead,
    dependencies=[Depends(require_database_admin)],
)
async def admin_update_report_status(
    report_id: str,
    payload: ReportStatusUpdate,
    service: ReportService = Depends(service_dependency),
) -> ReportRead:
    report = await service.update_status(report_id, payload.status)
    if not report:
        raise HTTPException(404, "Report not found")
    return to_report_read(report)


@router.delete(
    "/reports/{report_id}",
    status_code=204,
    dependencies=[Depends(require_database_admin)],
)
async def admin_delete_report(
    report_id: str,
    service: ReportService = Depends(service_dependency),
) -> Response:
    if not await service.delete_report(report_id):
        raise HTTPException(404, "Report not found")
    return Response(status_code=204)
