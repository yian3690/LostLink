from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ItemAttributes(BaseModel):
    category: str | None = None
    brand: str | None = None
    color: str | None = None
    distinctive_features: list[str] = Field(default_factory=list)
    normalized_description: str = ""


class ReportCreate(BaseModel):
    kind: Literal["lost", "found"]
    description: str = ""
    campus: str | None = None
    location: str | None = None
    occurred_at: datetime | None = None
    line_user_id: str | None = None
    image_base64: str | None = Field(
        default=None, description="Optional base64-encoded JPEG/PNG image"
    )


class ReportDescriptionUpdate(BaseModel):
    description: str = Field(min_length=1, max_length=2000)


class ReportStatusUpdate(BaseModel):
    status: Literal["open", "returned"]


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    description: str
    category: str | None
    brand: str | None
    color: str | None
    distinctive_features: list[str]
    campus: str | None
    location: str | None
    occurred_at: datetime | None
    created_at: datetime
    image_url: str | None = None


class MatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    lost_report_id: str
    found_report_id: str
    score: float
    decision: str
    score_breakdown: dict[str, float]
    reasons: list[str]
    created_at: datetime


class ReportCreated(BaseModel):
    report: ReportRead
    matches: list[MatchRead] = Field(default_factory=list)


class DashboardStats(BaseModel):
    open_lost: int
    open_found: int
    high_confidence_matches: int
    returned_items: int
    return_rate: float
