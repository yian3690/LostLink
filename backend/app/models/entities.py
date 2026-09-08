from datetime import datetime
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, utcnow


def new_id() -> str:
    return str(uuid4())


vector_type = JSON().with_variant(Vector(768), "postgresql")


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    line_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="student", index=True)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    reports: Mapped[list["ItemReport"]] = relationship(back_populates="user")


class ItemReport(TimestampMixin, Base):
    __tablename__ = "item_reports"
    __table_args__ = (
        Index("ix_reports_lookup", "kind", "status", "campus", "category"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str | None] = mapped_column(String(80), index=True)
    brand: Mapped[str | None] = mapped_column(String(80))
    color: Mapped[str | None] = mapped_column(String(80))
    distinctive_features: Mapped[list[str]] = mapped_column(JSON, default=list)
    campus: Mapped[str | None] = mapped_column(String(120), index=True)
    location: Mapped[str | None] = mapped_column(String(240))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User | None] = relationship(back_populates="reports")
    images: Mapped[list["ItemImage"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    embedding: Mapped["ItemEmbedding | None"] = relationship(
        back_populates="report", cascade="all, delete-orphan", uselist=False
    )


class ItemImage(TimestampMixin, Base):
    __tablename__ = "item_images"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    report_id: Mapped[str] = mapped_column(ForeignKey("item_reports.id"), index=True)
    object_path: Mapped[str] = mapped_column(String(500))
    thumbnail_path: Mapped[str | None] = mapped_column(String(500))
    mime_type: Mapped[str] = mapped_column(String(80), default="image/jpeg")
    scan_status: Mapped[str] = mapped_column(String(20), default="pending")

    report: Mapped[ItemReport] = relationship(back_populates="images")


class ItemEmbedding(TimestampMixin, Base):
    __tablename__ = "item_embeddings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    report_id: Mapped[str] = mapped_column(
        ForeignKey("item_reports.id"), unique=True, index=True
    )
    e5_text: Mapped[list[float] | None] = mapped_column(vector_type)
    siglip_text: Mapped[list[float] | None] = mapped_column(vector_type)
    siglip_image: Mapped[list[float] | None] = mapped_column(vector_type)
    e5_model: Mapped[str | None] = mapped_column(String(200))
    siglip_model: Mapped[str | None] = mapped_column(String(200))

    report: Mapped[ItemReport] = relationship(back_populates="embedding")


class MatchCandidate(TimestampMixin, Base):
    __tablename__ = "match_candidates"
    __table_args__ = (
        Index("ix_matches_lost_score", "lost_report_id", "score"),
        Index("ix_matches_found_score", "found_report_id", "score"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lost_report_id: Mapped[str] = mapped_column(
        ForeignKey("item_reports.id"), index=True
    )
    found_report_id: Mapped[str] = mapped_column(
        ForeignKey("item_reports.id"), index=True
    )
    score: Mapped[float] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(String(20), default="waiting", index=True)
    score_breakdown: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)


class Notification(TimestampMixin, Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    match_id: Mapped[str | None] = mapped_column(ForeignKey("match_candidates.id"))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(20), default="line")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Claim(TimestampMixin, Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    match_id: Mapped[str] = mapped_column(ForeignKey("match_candidates.id"), index=True)
    claimant_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    private_evidence: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(120), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(36))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

