"""Fixed EN facet templates for company engage research (employee + contractor)."""

from __future__ import annotations

import re
from typing import Any

from app.normalize.schema import SearchResultItem

ENRICH_PURPOSE = "engage"

# (facet_id, query_template with {name})
FACET_TEMPLATES: tuple[tuple[str, str], ...] = (
    (
        "culture",
        '"{name}" company culture OR values OR "work life balance" OR "working at"',
    ),
    (
        "hiring",
        '"{name}" interview process OR hiring OR careers OR "what to expect"',
    ),
    (
        "funding",
        '"{name}" funding OR "series" OR investment OR "raised" OR valuation',
    ),
    (
        "vendors",
        (
            '"{name}" contractors OR consultants OR freelancers OR vendors OR '
            '"professional services" OR "agency partner" OR "preferred vendor"'
        ),
    ),
    (
        "initiatives",
        (
            '"{name}" RFP OR procurement OR "digital transformation" OR '
            '"strategic initiative" OR "consulting engagement" OR "managed services"'
        ),
    ),
)

_PREFIX_RE = re.compile(
    r"^(?:about|company|компани[яи]|о компании|startup|organization|организация)\s+",
    re.I,
)
_QUOTES_RE = re.compile(r'^[\s"\'«»]+|[\s"\'«»]+$')


def company_name_for_facets(query: str) -> str:
    """Best-effort company name from the user/normalized query (no LLM)."""
    name = (query or "").strip()
    name = _PREFIX_RE.sub("", name).strip()
    # Strip wrapping quotes (possibly after removing a leading "about ").
    for _ in range(2):
        stripped = _QUOTES_RE.sub("", name).strip()
        if stripped == name:
            break
        name = stripped
    # Drop trailing noise like "linkedin" / bare URLs for facet quoting.
    if "linkedin.com" in name.lower() or name.lower().startswith("http"):
        return name
    # Keep first line / clause if the query is long free text.
    for sep in (".", "?", "!", "|", "—", " - "):
        if sep in name and len(name) > 80:
            name = name.split(sep, 1)[0].strip()
            break
    return name or (query or "").strip()


def build_facet_queries(company_name: str) -> list[tuple[str, str]]:
    """Return [(facet_id, serp_query), ...] for the engage research set."""
    name = (company_name or "").strip()
    if not name:
        return []
    return [(facet_id, template.format(name=name)) for facet_id, template in FACET_TEMPLATES]


def merge_primary_and_facet_hits(
    primary: list[SearchResultItem],
    facet_batches: list[tuple[str, list[SearchResultItem]]],
) -> list[SearchResultItem]:
    """Primary first, then facet hits; dedupe by URL (case-insensitive)."""
    seen: set[str] = set()
    out: list[SearchResultItem] = []

    def _add(item: SearchResultItem, *, facet: str | None = None) -> None:
        key = (item.url or "").strip().lower()
        if key:
            if key in seen:
                return
            seen.add(key)
        extra = dict(item.extra or {})
        if facet:
            extra["facet"] = facet
        out.append(
            SearchResultItem(
                title=item.title,
                url=item.url,
                snippet=item.snippet,
                source=item.source,
                content=item.content,
                extra=extra,
            )
        )

    for item in primary:
        _add(item, facet=None)
    for facet_id, hits in facet_batches:
        for item in hits:
            _add(item, facet=facet_id)
    return out


def facet_meta_entry(
    *,
    facet_id: str,
    query: str,
    hit_count: int,
    error: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": facet_id,
        "query": query,
        "hit_count": hit_count,
    }
    if error:
        entry["error"] = error
    return entry
