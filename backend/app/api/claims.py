from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.models.entities import AuditLog, Claim, ItemReport, MatchCandidate, User
from app.schemas.claims import ClaimCreate, ClaimRead, ClaimReview


router = APIRouter(prefix="/api/v1", tags=["claims"])


async def get_or_create_user(
    session: AsyncSession, line_user_id: str, role: str = "student"
) -> User:
    user = await session.scalar(select(User).where(User.line_user_id == line_user_id))
    if user:
        return user
    user = User(line_user_id=line_user_id, role=role)
    session.add(user)
    await session.flush()
    return user


@router.post("/matches/{match_id}/claims", response_model=ClaimRead, status_code=201)
async def create_claim(
    match_id: str,
    payload: ClaimCreate,
    session: AsyncSession = Depends(get_session),
) -> ClaimRead:
    match = await session.get(MatchCandidate, match_id)
    if not match:
        raise HTTPException(404, "Match not found")
    existing = await session.scalar(
        select(Claim).where(
            Claim.match_id == match_id,
            Claim.status == "pending",
        )
    )
    if existing:
        raise HTTPException(409, "A pending claim already exists")
    claimant = await get_or_create_user(session, payload.line_user_id)
    claim = Claim(
        match_id=match_id,
        claimant_user_id=claimant.id,
        private_evidence=payload.private_evidence,
    )
    session.add(claim)
    for report_id in (match.lost_report_id, match.found_report_id):
        report = await session.get(ItemReport, report_id)
        if report:
            report.status = "claim_pending"
    session.add(
        AuditLog(
            actor_user_id=claimant.id,
            action="claim.created",
            entity_type="claim",
            entity_id=claim.id,
            details={"match_id": match_id},
        )
    )
    await session.commit()
    await session.refresh(claim)
    return ClaimRead.model_validate(claim)


@router.post("/claims/{claim_id}/review", response_model=ClaimRead)
async def review_claim(
    claim_id: str,
    payload: ClaimReview,
    x_admin_key: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ClaimRead:
    if settings.admin_api_key and x_admin_key != settings.admin_api_key:
        raise HTTPException(401, "Invalid admin key")
    claim = await session.get(Claim, claim_id)
    if not claim:
        raise HTTPException(404, "Claim not found")
    match = await session.get(MatchCandidate, claim.match_id)
    if not match:
        raise HTTPException(409, "Claim match is missing")

    reviewer = await get_or_create_user(
        session, payload.reviewer_line_user_id, role="admin"
    )
    claim.status = "approved" if payload.approved else "rejected"
    claim.reviewed_by = reviewer.id
    claim.reviewed_at = datetime.now(timezone.utc)
    match.decision = "claimed" if payload.approved else "review"
    for report_id in (match.lost_report_id, match.found_report_id):
        report = await session.get(ItemReport, report_id)
        if report:
            report.status = "returned" if payload.approved else "open"
    session.add(
        AuditLog(
            actor_user_id=reviewer.id,
            action="claim.approved" if payload.approved else "claim.rejected",
            entity_type="claim",
            entity_id=claim.id,
            details={"match_id": match.id},
        )
    )
    await session.commit()
    await session.refresh(claim)
    return ClaimRead.model_validate(claim)

