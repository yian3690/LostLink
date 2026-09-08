from fastapi import APIRouter

from app.api.claims import router as claims_router
from app.api.admin_reports import router as admin_reports_router
from app.api.line_webhook import router as line_router
from app.api.reports import router as reports_router


api_router = APIRouter()
api_router.include_router(admin_reports_router)
api_router.include_router(reports_router)
api_router.include_router(claims_router)
api_router.include_router(line_router)
