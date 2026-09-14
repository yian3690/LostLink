from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ItemClassification(BaseModel):
    category: str | None = None
    recognition_confidence: float | None = Field(default=None, ge=0, le=1)
    item_name_candidates: list[str] = Field(default_factory=list, max_length=5)


class ItemAttributes(BaseModel):
    category: str | None = None
    brand: str | None = None
    color: str | None = None
    distinctive_features: list[str] = Field(default_factory=list)
    feature_confidences: dict[str, float] = Field(default_factory=dict)
    normalized_description: str = ""
    recognition_confidence: float | None = Field(default=None, ge=0, le=1)
    item_name_candidates: list[str] = Field(default_factory=list, max_length=5)
    visible_text: list[str] = Field(default_factory=list, max_length=20)


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


class OwnerReportUpdate(BaseModel):
    description: str = Field(min_length=1, max_length=2000)
    location: str | None = Field(default=None, max_length=240)
    occurred_at: datetime | None = None
    image_base64: str | None = Field(
        default=None,
        description="Optional replacement JPEG/PNG/WebP image; null keeps the current image",
    )


class OwnerReportResolve(BaseModel):
    found_report_id: str | None = Field(default=None, max_length=36)


class ReportFeaturesUpdate(OwnerReportUpdate):
    category: str | None = Field(default=None, max_length=80)
    brand: str | None = Field(default=None, max_length=80)
    color: str | None = Field(default=None, max_length=80)
    distinctive_features: list[str] = Field(default_factory=list, max_length=30)


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
    feature_confidences: dict[str, float]
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


class OwnerReportResolved(BaseModel):
    lost_report: ReportRead
    found_report: ReportRead | None = None


class DashboardStats(BaseModel):
    open_lost: int
    open_found: int
    high_confidence_matches: int
    returned_items: int
    return_rate: float
