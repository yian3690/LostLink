from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import CampusLocationAlias


DEFAULT_LOCATION_ALIASES: tuple[tuple[str, str], ...] = (
    ("學餐", "學生餐廳"),
    ("學生餐廳", "學生餐廳"),
    ("總圖", "圖書館"),
    ("總圖書館", "圖書館"),
    ("圖書館", "圖書館"),
    ("綜大", "綜合大樓"),
    ("綜合大樓", "綜合大樓"),
    ("教大", "教學大樓"),
    ("教學大樓", "教學大樓"),
    ("活中", "活動中心"),
    ("活動中心", "活動中心"),
    ("體育館", "體育館"),
)

_location_aliases: dict[str, str] = dict(DEFAULT_LOCATION_ALIASES)


async def seed_and_load_location_aliases(session: AsyncSession) -> None:
    existing = set(await session.scalars(select(CampusLocationAlias.alias)))
    for alias, canonical_name in DEFAULT_LOCATION_ALIASES:
        if alias not in existing:
            session.add(CampusLocationAlias(alias=alias, canonical_name=canonical_name))
    await session.commit()
    rows = list(await session.scalars(select(CampusLocationAlias)))
    _location_aliases.clear()
    _location_aliases.update(
        {row.alias.strip(): row.canonical_name.strip() for row in rows}
    )


def normalize_location_aliases(text: str) -> str:
    """Normalize known campus aliases without guessing an unknown place."""
    normalized = text
    for alias in sorted(_location_aliases, key=len, reverse=True):
        normalized = normalized.replace(alias, _location_aliases[alias])
    return normalized
