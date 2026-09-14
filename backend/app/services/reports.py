import base64
import binascii
import io
import re
from pathlib import Path

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import PROJECT_ROOT, Settings
from app.models.entities import (
    AuditLog,
    ItemEmbedding,
    ItemImage,
    ItemReport,
    MatchCandidate,
    Notification,
    Claim,
    User,
    new_id,
)
from app.schemas.reports import DashboardStats, OwnerReportUpdate, ReportCreate, ReportFeaturesUpdate
from app.services.ai import (
    CATEGORY_TERMS,
    MultimodalAnalyzer,
    get_embedding_service,
    normalize_category_value,
    normalize_color_value,
)
from app.services.matching import (
    MatchResult,
    container_subtype,
    location_score,
    score_reports,
)
from app.services.line import LineClient
from app.services.line import extract_location, extract_time_hint, time_hint_matches


class ReportService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.analyzer = MultimodalAnalyzer(settings)
        self.embeddings = get_embedding_service(settings)

    async def create(self, payload: ReportCreate) -> tuple[ItemReport, list[MatchCandidate]]:
        image_bytes = self._decode_image(payload.image_base64)
        if image_bytes:
            self._validate_image(image_bytes)
        attributes = await self.analyzer.analyze(payload.description, image_bytes)
        description = attributes.normalized_description or payload.description
        location = payload.location or extract_location(description)

        user = await self._get_or_create_user(payload.line_user_id)
        if user and payload.line_user_id != "web-guest" and not image_bytes:
            duplicate = await self._find_recent_duplicate(
                user.id,
                payload.kind,
                description,
                attributes.category,
                attributes.color,
                location,
            )
            if duplicate:
                if not duplicate.location and location:
                    duplicate.location = location
                    await self.session.commit()
                setattr(duplicate, "_was_deduplicated", True)
                return duplicate, await self.list_matches(duplicate.id)

        time_hint = extract_time_hint(description)
        report = ItemReport(
            user_id=user.id if user else None,
            kind=payload.kind,
            description=description,
            category=attributes.category,
            brand=attributes.brand,
            color=attributes.color,
            distinctive_features=attributes.distinctive_features,
            feature_confidences=attributes.feature_confidences,
            campus=payload.campus,
            location=location,
            occurred_at=payload.occurred_at or (time_hint[0] if time_hint else None),
        )
        self.session.add(report)
        await self.session.flush()

        e5_text = await self.embeddings.encode_e5(description)
        siglip_text = await self.embeddings.encode_siglip_text(description)
        siglip_image = (
            await self.embeddings.encode_siglip_image(image_bytes)
            if image_bytes
            else None
        )
        report.embedding = ItemEmbedding(
            e5_text=e5_text,
            siglip_text=siglip_text,
            siglip_image=siglip_image,
            e5_model=self.settings.e5_model,
            siglip_model=self.settings.siglip_model,
        )
        if image_bytes:
            object_path, thumbnail_path = self._store_image(report.id, image_bytes)
            self.session.add(
                ItemImage(
                    report_id=report.id,
                    object_path=object_path,
                    thumbnail_path=thumbnail_path,
                    mime_type="image/jpeg",
                    scan_status="demo-safe" if self.settings.demo_mode else "pending",
                )
            )
        await self.session.flush()

        matches = await self._find_matches(report)
        await self.session.commit()
        await self.session.refresh(report, attribute_names=["images"])
        await self._dispatch_notifications(matches)
        return report, matches

    async def search_found(
        self,
        description: str,
        image_bytes: bytes | None = None,
        location: str | None = None,
        limit: int = 3,
    ) -> list[tuple[ItemReport, MatchResult]]:
        """Search open found items without creating a lost-item database record."""
        if image_bytes:
            self._validate_image(image_bytes)
        attributes = await self.analyzer.analyze(description, image_bytes)
        generic_item_query = self._is_generic_item_query(description) and not image_bytes
        if generic_item_query:
            # Do not let place words (for example, "圖書館") make the LLM guess
            # an item category (for example, "book") when no item was supplied.
            attributes.category = "other"
        normalized = attributes.normalized_description or description
        time_hint = extract_time_hint(description)
        query_report = ItemReport(
            kind="lost",
            description=normalized,
            category=attributes.category,
            brand=attributes.brand,
            color=attributes.color,
            distinctive_features=attributes.distinctive_features,
            feature_confidences=attributes.feature_confidences,
            location=location or extract_location(description),
            occurred_at=time_hint[0] if time_hint else None,
        )
        siglip_text = await self.embeddings.encode_siglip_text(normalized)
        siglip_image = (
            await self.embeddings.encode_siglip_image(image_bytes)
            if image_bytes
            else None
        )
        query_report.embedding = ItemEmbedding(
            e5_text=await self.embeddings.encode_e5(normalized),
            # score_reports compares the lost-side SigLIP vector with the found
            # image vector. For a temporary photo query, use its image vector.
            siglip_text=siglip_image or siglip_text,
            siglip_image=siglip_image,
            e5_model=self.settings.e5_model,
            siglip_model=self.settings.siglip_model,
        )
        candidates = list(
            await self.session.scalars(
                select(ItemReport)
                .options(
                    selectinload(ItemReport.embedding),
                    selectinload(ItemReport.images),
                )
                .where(ItemReport.kind == "found", ItemReport.status == "open")
                .limit(100)
            )
        )
        equivalent_categories = {
            "bottle": {"bottle", "drink"},
            "drink": {"bottle", "drink"},
        }
        if query_report.category and query_report.category != "other":
            allowed = equivalent_categories.get(
                query_report.category,
                {query_report.category},
            )
            candidates = [item for item in candidates if item.category in allowed]
        query_container_subtype = container_subtype(query_report)
        if query_container_subtype:
            candidates = [
                item
                for item in candidates
                if container_subtype(item) in {None, query_container_subtype}
            ]
        location_was_used = bool(query_report.location)
        if query_report.location:
            candidates = [
                item
                for item in candidates
                if location_score(query_report.location, item.location) > 0
            ]
        time_was_used = bool(time_hint)
        if time_hint:
            center, uncertainty = time_hint
            close_in_time = []
            for item in candidates:
                item_time = item.occurred_at or item.created_at
                if time_hint_matches(item_time, center, uncertainty):
                    close_in_time.append(item)
            candidates = close_in_time

        ranked: list[tuple[ItemReport, MatchResult]] = []
        for candidate in candidates:
            result = score_reports(
                query_report,
                candidate,
                self.settings.match_notify_threshold,
                self.settings.match_review_threshold,
            )
            same_category = bool(
                query_report.category
                and candidate.category
                and candidate.category
                in equivalent_categories.get(
                    query_report.category,
                    {query_report.category},
                )
            )
            # A location-only query is useful even when the item category is not
            # known yet. Location filtering itself is sufficient evidence to list
            # candidates, while specific item queries still use similarity scores.
            has_explicit_filter = location_was_used or time_was_used
            floor = (
                0.0
                if has_explicit_filter and query_report.category == "other"
                else (0.22 if same_category else 0.42)
            )
            if result.score >= floor:
                ranked.append((candidate, result))
        return sorted(ranked, key=lambda pair: pair[1].score, reverse=True)[:limit]

    async def refine(
        self, report: ItemReport, detail: str
    ) -> tuple[ItemReport, list[MatchCandidate]]:
        """Add a conversational clue to an existing lost report and match again."""
        combined = f"{report.description}；補充線索：{detail.strip()}"
        attributes = await self.analyzer.analyze(combined)
        report.description = attributes.normalized_description or combined
        report.category = attributes.category or report.category
        report.brand = attributes.brand or report.brand
        report.color = attributes.color or report.color
        report.distinctive_features = list(
            dict.fromkeys([*report.distinctive_features, *attributes.distinctive_features])
        )
        report.feature_confidences = {
            **(report.feature_confidences or {}),
            **attributes.feature_confidences,
        }

        e5_text = await self.embeddings.encode_e5(report.description)
        siglip_text = await self.embeddings.encode_siglip_text(report.description)
        if report.embedding is None:
            report.embedding = ItemEmbedding(
                e5_text=e5_text,
                siglip_text=siglip_text,
                e5_model=self.settings.e5_model,
                siglip_model=self.settings.siglip_model,
            )
        else:
            report.embedding.e5_text = e5_text
            report.embedding.siglip_text = siglip_text
            report.embedding.e5_model = self.settings.e5_model
            report.embedding.siglip_model = self.settings.siglip_model

        await self.session.flush()
        matches = await self._find_matches(report)
        await self.session.commit()
        await self.session.refresh(report, attribute_names=["images"])
        await self._dispatch_notifications(matches)
        return report, matches

    async def replace_description(
        self, report_id: str, description: str
    ) -> tuple[ItemReport, list[MatchCandidate]] | None:
        report = await self.session.scalar(
            select(ItemReport)
            .options(
                selectinload(ItemReport.embedding),
                selectinload(ItemReport.images),
            )
            .where(ItemReport.id == report_id)
        )
        if not report:
            return None
        attributes = await self.analyzer.analyze(description.strip())
        report.description = attributes.normalized_description or description.strip()
        report.category = attributes.category
        report.brand = attributes.brand
        report.color = attributes.color
        report.distinctive_features = attributes.distinctive_features
        report.feature_confidences = attributes.feature_confidences

        e5_text = await self.embeddings.encode_e5(report.description)
        siglip_text = await self.embeddings.encode_siglip_text(report.description)
        if report.embedding is None:
            report.embedding = ItemEmbedding(
                e5_text=e5_text,
                siglip_text=siglip_text,
                e5_model=self.settings.e5_model,
                siglip_model=self.settings.siglip_model,
            )
        else:
            report.embedding.e5_text = e5_text
            report.embedding.siglip_text = siglip_text
            report.embedding.e5_model = self.settings.e5_model
            report.embedding.siglip_model = self.settings.siglip_model

        await self._clear_matches(report.id)
        await self.session.flush()
        matches = await self._find_matches(report)
        await self.session.commit()
        await self._dispatch_notifications(matches)
        return report, matches

    async def update_owned_report(
        self, report_id: str, line_user_id: str, payload: OwnerReportUpdate
    ) -> tuple[ItemReport, list[MatchCandidate]] | None:
        report = await self.session.scalar(
            select(ItemReport)
            .join(User, User.id == ItemReport.user_id)
            .options(selectinload(ItemReport.embedding), selectinload(ItemReport.images))
            .where(
                ItemReport.id == report_id,
                ItemReport.kind == "lost",
                User.line_user_id == line_user_id,
            )
        )
        if not report:
            return None
        image_bytes = self._decode_image(payload.image_base64)
        if image_bytes:
            self._validate_image(image_bytes)
        attributes = await self.analyzer.analyze(payload.description.strip(), image_bytes)
        report.description = attributes.normalized_description or payload.description.strip()
        report.category = attributes.category
        report.brand = attributes.brand
        report.color = attributes.color
        report.distinctive_features = attributes.distinctive_features
        report.feature_confidences = attributes.feature_confidences
        report.location = payload.location.strip() if payload.location and payload.location.strip() else None
        report.occurred_at = payload.occurred_at
        old_paths: list[str] = []
        if image_bytes:
            object_path, thumbnail_path = self._store_image(report.id, image_bytes)
            if report.images:
                current = report.images[0]
                old_paths.extend(
                    path for path in (current.object_path, current.thumbnail_path) if path
                )
                current.object_path = object_path
                current.thumbnail_path = thumbnail_path
                current.mime_type = "image/jpeg"
                current.scan_status = "demo-safe" if self.settings.demo_mode else "pending"
            else:
                report.images.append(
                    ItemImage(
                        object_path=object_path,
                        thumbnail_path=thumbnail_path,
                        mime_type="image/jpeg",
                        scan_status="demo-safe" if self.settings.demo_mode else "pending",
                    )
                )
        result = await self._reindex_updated_report(report, image_bytes)
        self._unlink_upload_paths(old_paths)
        return result

    async def replace_admin_details(
        self, report_id: str, payload: ReportFeaturesUpdate
    ) -> tuple[ItemReport, list[MatchCandidate]] | None:
        report = await self.session.scalar(
            select(ItemReport)
            .options(selectinload(ItemReport.embedding), selectinload(ItemReport.images))
            .where(ItemReport.id == report_id)
        )
        if not report:
            return None
        report.description = payload.description.strip()
        report.category = normalize_category_value(payload.category, payload.description)
        report.brand = payload.brand.strip() if payload.brand and payload.brand.strip() else None
        report.color = normalize_color_value(payload.color)
        report.distinctive_features = [
            value.strip()
            for value in payload.distinctive_features
            if value.strip()
        ][:30]
        report.feature_confidences = {
            feature: 1.0 for feature in report.distinctive_features
        }
        report.location = payload.location.strip() if payload.location and payload.location.strip() else None
        report.occurred_at = payload.occurred_at
        return await self._reindex_updated_report(report)

    async def resolve_owned_report(
        self,
        report_id: str,
        line_user_id: str,
        found_report_id: str | None = None,
    ) -> tuple[ItemReport, ItemReport | None] | None:
        lost = await self.session.scalar(
            select(ItemReport)
            .join(User, User.id == ItemReport.user_id)
            .options(selectinload(ItemReport.images))
            .where(
                ItemReport.id == report_id,
                ItemReport.kind == "lost",
                User.line_user_id == line_user_id,
            )
        )
        if not lost:
            return None

        found = None
        match = None
        if found_report_id:
            match = await self.session.scalar(
                select(MatchCandidate).where(
                    MatchCandidate.lost_report_id == lost.id,
                    MatchCandidate.found_report_id == found_report_id,
                )
            )
            if not match:
                raise ValueError("Selected found item is not matched to this search")
            found = await self.session.scalar(
                select(ItemReport)
                .options(selectinload(ItemReport.images))
                .where(
                    ItemReport.id == found_report_id,
                    ItemReport.kind == "found",
                )
            )
            if not found:
                raise ValueError("Selected found item no longer exists")
            found.status = "returned"

        related_match_filter = MatchCandidate.lost_report_id == lost.id
        if found:
            related_match_filter = or_(
                related_match_filter,
                MatchCandidate.found_report_id == found.id,
            )
        related_matches = list(
            await self.session.scalars(
                select(MatchCandidate).where(related_match_filter)
            )
        )
        reviewed_at = datetime.now(timezone.utc)
        for related_match in related_matches:
            is_selected = bool(match and related_match.id == match.id)
            related_match.decision = "claimed" if is_selected else "dismissed"
            pending_claims = list(
                await self.session.scalars(
                    select(Claim).where(
                        Claim.match_id == related_match.id,
                        Claim.status == "pending",
                    )
                )
            )
            for claim in pending_claims:
                claim.status = "approved" if is_selected else "rejected"
                claim.reviewed_by = lost.user_id
                claim.reviewed_at = reviewed_at

        lost.status = "returned"
        self.session.add(
            AuditLog(
                actor_user_id=lost.user_id,
                action="report.resolved_by_owner",
                entity_type="item_report",
                entity_id=lost.id,
                details={"found_report_id": found.id if found else None},
            )
        )
        await self.session.commit()
        return lost, found

    async def _reindex_updated_report(
        self, report: ItemReport, replacement_image: bytes | None = None
    ) -> tuple[ItemReport, list[MatchCandidate]]:
        embedding_text = "；".join(
            value
            for value in (
                report.description,
                report.category,
                report.color,
                report.brand,
                "、".join(report.distinctive_features),
            )
            if value
        )
        e5_text = await self.embeddings.encode_e5(embedding_text)
        siglip_text = await self.embeddings.encode_siglip_text(embedding_text)
        siglip_image = (
            await self.embeddings.encode_siglip_image(replacement_image)
            if replacement_image
            else None
        )
        if report.embedding is None:
            report.embedding = ItemEmbedding(
                e5_text=e5_text,
                siglip_text=siglip_text,
                siglip_image=siglip_image,
                e5_model=self.settings.e5_model,
                siglip_model=self.settings.siglip_model,
            )
        else:
            report.embedding.e5_text = e5_text
            report.embedding.siglip_text = siglip_text
            if replacement_image:
                report.embedding.siglip_image = siglip_image
            report.embedding.e5_model = self.settings.e5_model
            report.embedding.siglip_model = self.settings.siglip_model
        await self._clear_matches(report.id)
        await self.session.flush()
        matches = await self._find_matches(report)
        await self.session.commit()
        await self._dispatch_notifications(matches)
        return report, matches

    @staticmethod
    def _unlink_upload_paths(paths: list[str]) -> None:
        uploads_root = (PROJECT_ROOT / "uploads").resolve()
        for value in paths:
            target = (PROJECT_ROOT / value).resolve()
            if uploads_root in target.parents:
                target.unlink(missing_ok=True)

    async def delete_report(self, report_id: str) -> bool:
        report = await self.session.scalar(
            select(ItemReport)
            .options(
                selectinload(ItemReport.images),
                selectinload(ItemReport.embedding),
            )
            .where(ItemReport.id == report_id)
        )
        if not report:
            return False
        paths = [
            path
            for image in report.images
            for path in (image.object_path, image.thumbnail_path)
            if path
        ]
        await self._clear_matches(report.id)
        await self.session.delete(report)
        await self.session.commit()
        uploads_root = (PROJECT_ROOT / "uploads").resolve()
        for value in paths:
            target = (PROJECT_ROOT / value).resolve()
            if uploads_root in target.parents:
                target.unlink(missing_ok=True)
        return True

    async def update_status(self, report_id: str, status: str) -> ItemReport | None:
        report = await self.session.scalar(
            select(ItemReport)
            .options(selectinload(ItemReport.images))
            .where(ItemReport.id == report_id)
        )
        if not report:
            return None
        report.status = status
        await self.session.commit()
        return report

    async def _clear_matches(self, report_id: str) -> None:
        matches = list(
            await self.session.scalars(
                select(MatchCandidate).where(
                    or_(
                        MatchCandidate.lost_report_id == report_id,
                        MatchCandidate.found_report_id == report_id,
                    )
                )
            )
        )
        match_ids = [match.id for match in matches]
        if not match_ids:
            return
        await self.session.execute(delete(Claim).where(Claim.match_id.in_(match_ids)))
        await self.session.execute(
            delete(Notification).where(Notification.match_id.in_(match_ids))
        )
        await self.session.execute(
            delete(MatchCandidate).where(MatchCandidate.id.in_(match_ids))
        )

    async def list_reports(
        self, limit: int = 50, kind: str | None = None
    ) -> list[ItemReport]:
        query = (
            select(ItemReport)
            .options(selectinload(ItemReport.images))
            .order_by(ItemReport.created_at.desc())
            .limit(limit)
        )
        if kind:
            query = query.where(ItemReport.kind == kind)
        result = await self.session.scalars(query)
        return list(result)

    async def list_user_reports(
        self, line_user_id: str, limit: int = 50, kind: str | None = None
    ) -> list[ItemReport]:
        query = (
            select(ItemReport)
            .join(User, User.id == ItemReport.user_id)
            .options(selectinload(ItemReport.images))
            .where(User.line_user_id == line_user_id)
            .order_by(ItemReport.created_at.desc())
            .limit(limit)
        )
        if kind:
            query = query.where(ItemReport.kind == kind)
        result = await self.session.scalars(query)
        return list(result)

    async def public_thumbnail(self, report_id: str) -> Path | None:
        report = await self.session.scalar(
            select(ItemReport)
            .options(selectinload(ItemReport.images))
            .where(
                ItemReport.id == report_id,
                ItemReport.kind == "found",
                ItemReport.status.in_(("open", "claim_pending", "returned")),
            )
        )
        if not report or not report.images or not report.images[0].thumbnail_path:
            return None
        uploads_root = (PROJECT_ROOT / "uploads").resolve()
        thumbnail = (PROJECT_ROOT / report.images[0].thumbnail_path).resolve()
        if uploads_root not in thumbnail.parents:
            return None
        return thumbnail if thumbnail.is_file() else None

    async def protected_thumbnail(self, report_id: str) -> Path | None:
        report = await self.session.scalar(
            select(ItemReport)
            .options(selectinload(ItemReport.images))
            .where(ItemReport.id == report_id)
        )
        return self._thumbnail_path(report)

    async def owner_thumbnail(self, report_id: str, line_user_id: str) -> Path | None:
        report = await self.session.scalar(
            select(ItemReport)
            .join(User, User.id == ItemReport.user_id)
            .options(selectinload(ItemReport.images))
            .where(ItemReport.id == report_id, User.line_user_id == line_user_id)
        )
        return self._thumbnail_path(report)

    @staticmethod
    def _thumbnail_path(report: ItemReport | None) -> Path | None:
        if not report or not report.images or not report.images[0].thumbnail_path:
            return None
        uploads_root = (PROJECT_ROOT / "uploads").resolve()
        thumbnail = (PROJECT_ROOT / report.images[0].thumbnail_path).resolve()
        if uploads_root not in thumbnail.parents:
            return None
        return thumbnail if thumbnail.is_file() else None

    async def list_matches(self, report_id: str) -> list[MatchCandidate]:
        result = await self.session.scalars(
            select(MatchCandidate)
            .where(
                or_(
                    MatchCandidate.lost_report_id == report_id,
                    MatchCandidate.found_report_id == report_id,
                ),
                MatchCandidate.decision != "dismissed",
            )
            .order_by(MatchCandidate.score.desc())
        )
        visible: list[MatchCandidate] = []
        for match in result:
            lost = await self.session.get(ItemReport, match.lost_report_id)
            found = await self.session.get(ItemReport, match.found_report_id)
            same_category = bool(
                lost and found and lost.category and lost.category == found.category
            )
            floor = 0.30 if same_category else max(0.55, self.settings.match_review_threshold)
            if match.score >= floor:
                visible.append(match)
        return visible

    async def stats(self) -> DashboardStats:
        async def count(statement) -> int:
            return int((await self.session.scalar(statement)) or 0)

        open_lost = await count(
            select(func.count()).select_from(ItemReport).where(
                ItemReport.kind == "lost", ItemReport.status == "open"
            )
        )
        open_found = await count(
            select(func.count()).select_from(ItemReport).where(
                ItemReport.kind == "found", ItemReport.status == "open"
            )
        )
        high = await count(
            select(func.count()).select_from(MatchCandidate).where(
                MatchCandidate.decision == "notify"
            )
        )
        returned = await count(
            select(func.count()).select_from(ItemReport).where(
                ItemReport.kind == "lost", ItemReport.status == "returned"
            )
        )
        total_lost = open_lost + returned
        return DashboardStats(
            open_lost=open_lost,
            open_found=open_found,
            high_confidence_matches=high,
            returned_items=returned,
            return_rate=round(returned / total_lost, 4) if total_lost else 0.0,
        )

    async def _find_matches(self, report: ItemReport) -> list[MatchCandidate]:
        opposite = "found" if report.kind == "lost" else "lost"
        query = (
            select(ItemReport)
            .options(selectinload(ItemReport.embedding))
            .where(ItemReport.kind == opposite, ItemReport.status == "open")
        )
        if report.campus:
            query = query.where(
                or_(ItemReport.campus == report.campus, ItemReport.campus.is_(None))
            )
        if (
            self.session.bind
            and self.session.bind.dialect.name == "postgresql"
            and report.embedding
            and report.embedding.e5_text
        ):
            query = (
                query.join(ItemEmbedding, ItemEmbedding.report_id == ItemReport.id)
                .where(ItemEmbedding.e5_text.is_not(None))
                .order_by(
                    ItemEmbedding.e5_text.op("<=>")(report.embedding.e5_text)
                )
            )
        candidates = list(await self.session.scalars(query.limit(100)))
        matches: list[MatchCandidate] = []
        for candidate in candidates:
            if report.category and candidate.category and report.category != candidate.category:
                continue
            lost, found = (report, candidate) if report.kind == "lost" else (candidate, report)
            result = score_reports(
                lost,
                found,
                self.settings.match_notify_threshold,
                self.settings.match_review_threshold,
            )
            same_category = bool(
                report.category
                and candidate.category
                and report.category == candidate.category
            )
            candidate_floor = (
                0.30
                if same_category
                else max(0.55, self.settings.match_review_threshold)
            )
            if result.score < candidate_floor:
                continue
            existing = await self.session.scalar(
                select(MatchCandidate).where(
                    MatchCandidate.lost_report_id == lost.id,
                    MatchCandidate.found_report_id == found.id,
                )
            )
            match = existing or MatchCandidate(
                lost_report_id=lost.id, found_report_id=found.id, score=0
            )
            match.score = result.score
            match.decision = result.decision
            match.score_breakdown = result.breakdown
            match.reasons = result.reasons
            if existing is None:
                self.session.add(match)
            matches.append(match)
        await self.session.flush()
        ranked = sorted(matches, key=lambda item: item.score, reverse=True)
        # A found item can have only one true owner. Notify only the single
        # highest-confidence lost report; keep other candidates for admin review.
        best = next((item for item in ranked if item.decision in {"notify", "review"}), None)
        # A newly created lost report already receives its candidates in the
        # webhook/API response. Only a later found report should create a Push
        # notification; otherwise the owner receives the same result twice.
        if report.kind == "found" and best:
            lost = await self.session.get(ItemReport, best.lost_report_id)
            existing_notification = await self.session.scalar(
                select(Notification).where(Notification.match_id == best.id)
            )
            if lost and lost.user_id and not existing_notification:
                self.session.add(
                    Notification(
                        match_id=best.id,
                        user_id=lost.user_id,
                        payload={
                            "message": "找到一個可能與你的物品相符的失物。",
                            "score": best.score,
                            "reasons": best.reasons,
                        },
                    )
                )
        await self.session.flush()
        return ranked

    async def _dispatch_notifications(
        self, matches: list[MatchCandidate]
    ) -> None:
        if not self.settings.line_channel_access_token:
            return
        client = LineClient(self.settings)
        for match in matches:
            if match.decision not in {"notify", "review"}:
                continue
            notification = await self.session.scalar(
                select(Notification).where(
                    Notification.match_id == match.id,
                    Notification.status == "pending",
                )
            )
            if not notification or not notification.user_id:
                continue
            user = await self.session.get(User, notification.user_id)
            if not user:
                continue
            try:
                await client.push(
                    user.line_user_id,
                    [
                        {
                            "type": "text",
                            "text": (
                                "有人撿到一件可能和你的遺失物相似的東西，請先確認照片與特徵。"
                                f"\n配對信心：{match.score:.0%}"
                                f"\n原因：{'、'.join(match.reasons) or '多項特徵接近'}"
                                "\n這只是候選結果，不代表已確認為你的物品。"
                            ),
                            "quickReply": {
                                "items": [
                                    {"type": "action", "action": {"type": "message", "label": "查看候選照片", "text": "再給我看一次照片"}},
                                    {"type": "action", "action": {"type": "message", "label": "不是我的", "text": "這不是我的"}},
                                ]
                            },
                        }
                    ],
                )
            except Exception:
                notification.attempts += 1
                notification.status = "retry"
            else:
                notification.attempts += 1
                notification.status = "sent"
                notification.sent_at = datetime.now(timezone.utc)
        await self.session.commit()

    async def _get_or_create_user(self, line_user_id: str | None) -> User | None:
        if not line_user_id:
            return None
        user = await self.session.scalar(
            select(User).where(User.line_user_id == line_user_id)
        )
        if user:
            return user
        user = User(line_user_id=line_user_id)
        self.session.add(user)
        await self.session.flush()
        return user

    async def _find_recent_duplicate(
        self,
        user_id: str,
        kind: str,
        description: str,
        category: str | None,
        color: str | None,
        location: str | None,
    ) -> ItemReport | None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        candidates = list(
            await self.session.scalars(
                select(ItemReport)
                .options(
                    selectinload(ItemReport.images),
                    selectinload(ItemReport.embedding),
                )
                .where(
                    ItemReport.user_id == user_id,
                    ItemReport.kind == kind,
                    ItemReport.status.in_(("open", "claim_pending")),
                    ItemReport.created_at >= cutoff,
                )
                .order_by(ItemReport.created_at.desc())
                .limit(20)
            )
        )
        key = self._duplicate_key(description)
        for candidate in candidates:
            if key and key == self._duplicate_key(candidate.description):
                return candidate
            same_category = bool(
                category and candidate.category and category == candidate.category
            )
            compatible_color = not color or not candidate.color or color == candidate.color
            same_location = bool(
                location
                and candidate.location
                and self._duplicate_key(location)
                == self._duplicate_key(candidate.location)
            )
            if same_category and compatible_color and same_location:
                return candidate
        return None

    @staticmethod
    def _duplicate_key(text: str) -> str:
        normalized = text.casefold()
        for phrase in (
            "請幫我找",
            "我的",
            "我有",
            "我",
            "不見了",
            "不見",
            "找不到",
            "弄丟了",
            "弄丟",
            "遺失了",
            "遺失",
            "掉了",
            "在",
        ):
            normalized = normalized.replace(phrase, "")
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)

    @staticmethod
    def _contains_specific_item(text: str) -> bool:
        normalized = text.casefold()
        return any(
            term.casefold() in normalized
            for terms in CATEGORY_TERMS.values()
            for term in terms
        )

    @staticmethod
    def _is_generic_item_query(text: str) -> bool:
        if not any(term in text for term in ("東西", "物品", "某樣", "某個")):
            return False
        return not ReportService._contains_specific_item(text)

    @staticmethod
    def _decode_image(encoded: str | None) -> bytes | None:
        if not encoded:
            return None
        if "," in encoded:
            encoded = encoded.split(",", 1)[1]
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image_base64 is not valid base64") from exc
        if len(data) > 15 * 1024 * 1024:
            raise ValueError("image must be 15 MB or smaller")
        return data

    @staticmethod
    def _validate_image(data: bytes) -> None:
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("unsupported image format")
                image.verify()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ValueError(
                "image must be a valid JPEG, PNG, or WebP file"
            ) from exc

    @staticmethod
    def _store_image(report_id: str, data: bytes) -> tuple[str, str]:
        from PIL import Image, ImageOps, UnidentifiedImageError

        folder = PROJECT_ROOT / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{report_id}-{new_id()}.jpg"
        path = folder / filename
        thumbnail_name = f"{report_id}-{new_id()}-public.jpg"
        thumbnail_path = folder / thumbnail_name
        try:
            with Image.open(io.BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((2048, 2048))
                image.save(path, "JPEG", quality=90, optimize=True)
                thumbnail = image.copy()
                thumbnail.thumbnail((720, 720))
                thumbnail.save(thumbnail_path, "JPEG", quality=78, optimize=True)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            path.unlink(missing_ok=True)
            thumbnail_path.unlink(missing_ok=True)
            raise ValueError("image must be a valid JPEG, PNG, or WebP file") from exc
        return (
            str(Path("uploads") / filename),
            str(Path("uploads") / thumbnail_name),
        )
