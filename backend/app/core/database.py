from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.models.base import Base


settings = get_settings()
engine_kwargs: dict[str, object] = {"pool_pre_ping": True}
if settings.database_url.endswith(":memory:"):
    engine_kwargs["poolclass"] = StaticPool

engine = create_async_engine(settings.database_url, **engine_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_database() -> None:
    async with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all)
        if connection.dialect.name == "postgresql":
            await connection.execute(
                text(
                    "ALTER TABLE item_reports ADD COLUMN IF NOT EXISTS "
                    "feature_confidences JSONB NOT NULL DEFAULT '{}'::jsonb"
                )
            )
        elif connection.dialect.name == "sqlite":
            columns = await connection.execute(text("PRAGMA table_info(item_reports)"))
            if "feature_confidences" not in {row[1] for row in columns.fetchall()}:
                await connection.execute(
                    text(
                        "ALTER TABLE item_reports ADD COLUMN feature_confidences "
                        "JSON NOT NULL DEFAULT '{}'"
                    )
                )
    from app.services.locations import seed_and_load_location_aliases

    async with SessionLocal() as session:
        await seed_and_load_location_aliases(session)


async def close_database() -> None:
    await engine.dispose()
