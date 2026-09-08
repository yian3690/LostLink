import base64
import binascii
import io
from pathlib import Path

from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import PROJECT_ROOT, Settings
from app.models.entities import (
    ItemEmbedding,
    ItemImage,
    ItemReport,
    MatchCandidate,
    Notification,
    Claim,
    User,
    new_id,
)
from app.schemas.reports import DashboardStats, ReportCreate
from app.services.ai import MultimodalAnalyzer, get_embedding_service
from app.services.matching import score_reports
from app.services.line import LineClient


class ReportService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.analyzer = MultimodalAnalyzer(settings)
        self.embeddings = get_embedding_service(settings)

    async def create(self, payload: ReportCreate) -> tuple[ItemReport, list[MatchCandidate]]:
        image_bytes = self._decode_image(payload.image_base64)
        attributes = await self.analyzer.analyze(payload.description, image_bytes)
        description = attributes.normalized_description or payload.description

        user = await self._get_or_create_user(payload.line_user_id)
        report = ItemReport(
            user_id=user.id if user else None,
            kind=payload.kind,
            description=description,
            category=attributes.category,
            brand=attributes.brand,
            color=attributes.color,
            distinctive_features=attributes.distinctive_features,
            campus=payload.campus,
            location=payload.location,
            occurred_at=payload.occurred_at,
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

    async def public_thumbnail(self, report_id: str) -> Path | None:
        report = await self.session.scalar(
            select(ItemReport)
            .options(selectinload(ItemReport.images))
            .where(
                ItemReport.id == report_id,
                ItemReport.kind == "found",
                ItemReport.status.in_(("open", "claim_pending")),
            )
        )
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
                )
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
            if result.decision == "notify" and lost.user_id:
                self.session.add(
                    Notification(
                        match_id=match.id,
                        user_id=lost.user_id,
                        payload={
                            "message": "找到一個可能與你的物品相符的失物。",
                            "score": result.score,
                            "reasons": result.reasons,
                        },
                    )
                )
        await self.session.flush()
        return sorted(matches, key=lambda item: item.score, reverse=True)

    async def _dispatch_notifications(
        self, matches: list[MatchCandidate]
    ) -> None:
        if not self.settings.line_channel_access_token:
            return
        client = LineClient(self.settings)
        for match in matches:
            if match.decision != "notify":
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
                    "找到一個可能與你的物品相符的失物。"
                    f"\n配對信心：{match.score:.0%}"
                    f"\n原因：{'、'.join(match.reasons) or '多項特徵接近'}",
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
