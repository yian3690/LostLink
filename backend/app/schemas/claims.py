from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ClaimCreate(BaseModel):
    line_user_id: str
    private_evidence: str = Field(min_length=3, max_length=1000)


class ClaimReview(BaseModel):
    approved: bool
    reviewer_line_user_id: str


class ClaimRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    match_id: str
    claimant_user_id: str | None
    private_evidence: str
    status: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime

