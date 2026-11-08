"""LinkedIn person id extraction and person_search query helpers."""

from __future__ import annotations

import re
from urllib.parse import unquote

# Appended by ProviderManager for person_search SERP fallbacks.
LINKEDIN_PEOPLE_SITE = "site:linkedin.com/in"

_PERSON_URL_RE = re.compile(
    r"(?:https?://)?(?:[\w.-]+\.)?linkedin\.com/in/([^/?#\s]+)",
    re.I,
)

# Public vanity URLs / docs ids: rbranson, dobryakov, ada-lovelace
_BARE_SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-_]{0,100}$")


def linkedin_people_query(query: str) -> str:
    """Ensure person_search SERP queries include LinkedIn /in/ site restriction."""
    q = (query or "").strip()
    if not q:
        return q
    if LINKEDIN_PEOPLE_SITE in q.lower():
        return q
    return f"{q} {LINKEDIN_PEOPLE_SITE}"


def linkedin_person_slug(query: str) -> str | None:
    """Return LinkedIn person id for Scrapingdog ``/profile/?type=profile&id=``.

    Accepts only a profile URL (``linkedin.com/in/<id>``) or a single bare slug
    token (``rbranson``). Free-text names (``Grigoriy Dobryakov``) return None —
    those go through the cheap SERP chain with ``site:linkedin.com/in``.
    """
    q = (query or "").strip()
    if not q:
        return None

    # Manager may have appended site: for the SERP fallback chain.
    q = re.sub(rf"\s*{re.escape(LINKEDIN_PEOPLE_SITE)}\s*", " ", q, flags=re.I).strip()
    q = re.sub(r"\s+", " ", q).strip().strip("?.!,;:\"'")
    if not q:
        return None

    match = _PERSON_URL_RE.search(q)
    if match:
        slug = unquote(match.group(1)).strip().strip("/")
        return slug or None

    # Bare id only — no spaces, no "First Last".
    if " " in q or "/" in q:
        return None
    if _BARE_SLUG_RE.fullmatch(q):
        return q.lower()
    return None
