"""Scrapingdog LinkedIn Profile API — shared fetch + forced-endpoint helpers.

Forced ``POST /v1/linkedin/profile`` uses only this path (no SERP / fallback).
``scrapingdog_people`` / ``scrapingdog_company`` reuse the same HTTP call.
"""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import unquote

from app.config import Settings
from app.providers.company_slug import linkedin_company_slug
from app.providers.errors import ProviderEndpointError
from app.providers.http_util import get_http_client
from app.providers.person_slug import linkedin_person_slug

ProfileKind = Literal["person", "company"]
ScrapingdogProfileType = Literal["profile", "company"]

PROVIDER_NAME = "scrapingdog_profile"
# Same Scrapingdog key / upstream — share cooldown with search-chain providers.
COOLDOWN_ALIASES = (
    PROVIDER_NAME,
    "scrapingdog_people",
    "scrapingdog_company",
)

_BARE_SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-_]{0,100}$")
_PERSON_URL_RE = re.compile(
    r"(?:https?://)?(?:[\w.-]+\.)?linkedin\.com/in/([^/?#\s]+)",
    re.I,
)
_COMPANY_URL_RE = re.compile(
    r"(?:https?://)?(?:[\w.-]+\.)?linkedin\.com/company/([^/?#\s]+)",
    re.I,
)


class LinkedInProfileValidationError(ValueError):
    """Client error: missing/ambiguous id|url or free-text instead of slug."""


def scrapingdog_type_for(kind: ProfileKind) -> ScrapingdogProfileType:
    return "profile" if kind == "person" else "company"


def resolve_premium(settings: Settings, premium: bool | None) -> bool:
    if premium is not None:
        return bool(premium)
    return bool(settings.scrapingdog_linkedin_premium)


def resolve_profile_id(
    *,
    kind: ProfileKind,
    profile_id: str | None,
    url: str | None,
) -> tuple[str, str]:
    """Return ``(slug, canonical_url)``. Strict: bare slug or LinkedIn URL only."""
    raw_id = (profile_id or "").strip() or None
    raw_url = (url or "").strip() or None
    if raw_id and raw_url:
        raise LinkedInProfileValidationError("Provide either id or url, not both")
    if not raw_id and not raw_url:
        raise LinkedInProfileValidationError("Provide id or url")

    if raw_url:
        if kind == "person":
            match = _PERSON_URL_RE.search(raw_url)
            if not match:
                raise LinkedInProfileValidationError(
                    "url must be a LinkedIn person profile (linkedin.com/in/<id>)"
                )
            slug = unquote(match.group(1)).strip().strip("/")
            if not slug:
                raise LinkedInProfileValidationError("Could not parse person id from url")
            return slug, f"https://www.linkedin.com/in/{slug}"
        match = _COMPANY_URL_RE.search(raw_url)
        if not match:
            raise LinkedInProfileValidationError(
                "url must be a LinkedIn company page (linkedin.com/company/<id>)"
            )
        slug = unquote(match.group(1)).strip().strip("/")
        if not slug:
            raise LinkedInProfileValidationError("Could not parse company id from url")
        return slug, f"https://www.linkedin.com/company/{slug}"

    assert raw_id is not None
    # Free-text ("JetBrains CEO", "who is X") is not a Profile API id.
    if " " in raw_id or "/" in raw_id or "://" in raw_id.lower():
        raise LinkedInProfileValidationError(
            "id must be a LinkedIn vanity slug (single token), not free-text; "
            "use url for a LinkedIn profile/company link"
        )
    if not _BARE_SLUG_RE.fullmatch(raw_id):
        raise LinkedInProfileValidationError(
            "id must be a LinkedIn vanity slug (letters, digits, hyphen, underscore)"
        )
    slug = raw_id.lower()
    # Extra safety: keep parity with search helpers for URL-shaped tokens.
    if kind == "person":
        parsed = linkedin_person_slug(slug)
        if not parsed:
            raise LinkedInProfileValidationError("id is not a valid LinkedIn person slug")
        slug = parsed
        return slug, f"https://www.linkedin.com/in/{slug}"
    parsed = linkedin_company_slug(slug)
    if not parsed:
        raise LinkedInProfileValidationError("id is not a valid LinkedIn company slug")
    slug = parsed
    return slug, f"https://www.linkedin.com/company/{slug}"


async def fetch_scrapingdog_profile(
    *,
    settings: Settings,
    kind: ProfileKind,
    profile_id: str,
    premium: bool = True,
) -> tuple[Any, list[dict[str, Any]]]:
    """Call Scrapingdog ``GET /profile/?type=profile|company&id=…``.

    Returns ``(json_payload, endpoint_attempts)``. Raises ``ProviderEndpointError``
    on upstream HTTP >= 400 (caller applies cooldown).
    """
    api_key = settings.scrapingdog_api_key.strip()
    if not api_key:
        raise ProviderEndpointError(
            "scrapingdog profile unavailable: SCRAPINGDOG_API_KEY not configured",
            endpoint="profile",
            endpoint_attempts=[
                {
                    "endpoint": "profile",
                    "type": scrapingdog_type_for(kind),
                    "skipped": True,
                    "reason": "no_api_key",
                }
            ],
        )

    sd_type = scrapingdog_type_for(kind)
    params: dict[str, str] = {
        "api_key": api_key,
        "type": sd_type,
        "id": profile_id,
    }
    if premium:
        params["premium"] = "true"

    async with get_http_client() as client:
        resp = await client.get("https://api.scrapingdog.com/profile/", params=params)

    endpoint_attempts = [
        {
            "endpoint": "profile",
            "type": sd_type,
            "id": profile_id,
            "premium": premium,
            "status_code": resp.status_code,
        }
    ]
    if resp.status_code >= 400:
        detail = resp.text[:300]
        raise ProviderEndpointError(
            f"scrapingdog profile {kind} failed status={resp.status_code} "
            f"id={profile_id!r} body={detail}",
            endpoint="profile",
            endpoint_attempts=endpoint_attempts,
        )

    return resp.json(), endpoint_attempts


def unwrap_profile_payload(data: Any) -> Any:
    """Scrapingdog sometimes wraps a single object in a one-element list."""
    if isinstance(data, list):
        if len(data) == 1:
            return data[0]
        return data
    return data
