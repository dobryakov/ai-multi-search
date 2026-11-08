"""Derive a LinkedIn company universal_name / slug from a free-text query."""

from __future__ import annotations

import re
from urllib.parse import unquote

_COMPANY_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?linkedin\.com/company/([^/?#\s]+)",
    re.I,
)

_CORP_TOKENS = {
    "inc",
    "inc.",
    "llc",
    "ltd",
    "ltd.",
    "gmbh",
    "corp",
    "corp.",
    "co",
    "co.",
    "ooo",
    "ооо",
    "ao",
    "ао",
    "pao",
    "пао",
    "gk",
    "гк",
    "group",
    "группа",
    "компани",
    "компания",
    "the",
}


def linkedin_company_slug(query: str) -> str | None:
    """Return LinkedIn company slug/id suitable for Scrapingdog ``/profile/?type=company&id=``.

    This is NOT a free-text company search: Scrapingdog Profile API needs the
    LinkedIn universal name (e.g. ``amazon``), not ``Amazon Inc``.
    """
    q = (query or "").strip()
    if not q:
        return None

    match = _COMPANY_URL_RE.search(q)
    if match:
        slug = unquote(match.group(1)).strip().strip("/")
        return slug or None

    # Already a slug: amazon, askona-group
    if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9\-]{0,80}", q):
        return q.lower()

    parts = [p.strip(".,") for p in re.split(r"\s+", q) if p.strip(".,")]
    parts = [p for p in parts if p.lower() not in _CORP_TOKENS]
    if not parts or len(parts) > 3:
        return None
    # Latin / digit tokens only — Cyrillic names have no stable LinkedIn slug guess.
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9&\-]*", p) for p in parts):
        return None
    slug = "-".join(p.lower().replace("&", "and") for p in parts)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or None
