import html
import re

import httpx

from app.core.config import Settings


WIKIPEDIA_API = "https://zh.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"


def safe_lookup_terms(brand: str | None, visible_text: list[str]) -> list[str]:
    """Keep useful product text while excluding common personal-data patterns."""
    values = [brand, *visible_text]
    safe: list[str] = []
    for value in values:
        term = re.sub(r"\s+", " ", (value or "").strip())[:60]
        if len(term) < 2:
            continue
        if re.search(r"(?:https?://|www\.|\S+@\S+|\b\d{8,}\b)", term, re.IGNORECASE):
            continue
        if term not in safe:
            safe.append(term)
    return safe[:4]


class TextOnlyItemLookup:
    """Query Wikimedia using only locally extracted brand/visible text."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def search(self, brand: str | None, visible_text: list[str]) -> list[str]:
        terms = safe_lookup_terms(brand, visible_text)
        if not self.settings.item_web_lookup_enabled or not terms:
            return []
        wikipedia_query = " OR ".join(f'"{term}"' for term in terms)
        wikidata_query = terms[0]
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.item_web_lookup_timeout_seconds,
                headers={"User-Agent": self.settings.item_web_lookup_user_agent},
            ) as client:
                wikipedia, wikidata = await self._request_sources(
                    client, wikipedia_query, wikidata_query
                )
        except (httpx.HTTPError, ValueError):
            return []
        return [*wikipedia, *wikidata][:6]

    @staticmethod
    async def _request_sources(
        client: httpx.AsyncClient,
        wikipedia_query: str,
        wikidata_query: str,
    ) -> tuple[list[str], list[str]]:
        wikipedia_rows: list[dict] = []
        wikidata_rows: list[dict] = []
        try:
            response = await client.get(
                WIKIPEDIA_API,
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": wikipedia_query,
                    "srlimit": 3,
                    "format": "json",
                    "utf8": 1,
                },
            )
            response.raise_for_status()
            wikipedia_rows = response.json().get("query", {}).get("search", [])
        except (httpx.HTTPError, ValueError):
            pass
        try:
            response = await client.get(
                WIKIDATA_API,
                params={
                    "action": "wbsearchentities",
                    "search": wikidata_query,
                    "language": "zh-tw",
                    "uselang": "zh-tw",
                    "limit": 3,
                    "format": "json",
                },
            )
            response.raise_for_status()
            wikidata_rows = response.json().get("search", [])
        except (httpx.HTTPError, ValueError):
            pass
        wikipedia = [
            TextOnlyItemLookup._clean_evidence(
                str(row.get("title") or ""), str(row.get("snippet") or "")
            )
            for row in wikipedia_rows[:3]
        ]
        wikidata = [
            TextOnlyItemLookup._clean_evidence(
                str(row.get("label") or ""), str(row.get("description") or "")
            )
            for row in wikidata_rows[:3]
        ]
        return [value for value in wikipedia if value], [value for value in wikidata if value]

    @staticmethod
    def _clean_evidence(title: str, description: str) -> str:
        clean_description = re.sub(r"<[^>]+>", " ", description)
        clean_description = re.sub(
            r"\s+", " ", html.unescape(clean_description)
        ).strip()
        clean_title = title.strip()
        return f"{clean_title}：{clean_description}"[:360] if clean_title else ""
