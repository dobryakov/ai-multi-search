from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

from app.config import Settings, get_settings
from app.normalize.schema import SearchResultItem
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.company_slug import linkedin_company_slug
from app.providers.errors import ProviderEndpointError
from app.providers.http_util import get_http_client
from app.providers.person_name import extract_person_name

logger = logging.getLogger(__name__)

_API_KEY_RE = re.compile(r"(api_key=)[^&]+", re.I)


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _redact_secrets(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_secrets(v) for v in value]
    if isinstance(value, str) and "api_key=" in value.lower():
        return _API_KEY_RE.sub(r"\1REDACTED", value)
    return value


def _clean_url(url: str) -> str:
    if not url:
        return url
    parsed = urlparse(url)
    if not parsed.query:
        return url
    qs = parse_qs(parsed.query, keep_blank_values=True)
    if "api_key" in qs:
        qs.pop("api_key", None)
        query = "&".join(f"{k}={v[0]}" for k, v in qs.items())
        return urlunparse(parsed._replace(query=query))
    if "api_key=" in url.lower():
        return _API_KEY_RE.sub(r"\1REDACTED", url)
    return url


class ScrapingdogPeopleProvider(BaseProvider):
    """LinkedIn people search only. On failure, let cheaper providers handle web search."""

    name = "scrapingdog_people"
    capability = "person_search"
    priority = 10
    cost = 40

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return bool(self.settings.scrapingdog_api_key.strip())

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        parsed = extract_person_name(request.query)
        if not parsed:
            raise ProviderEndpointError(
                "scrapingdog linkedin/search people skipped: "
                "query is not a person name (need first_name + last_name)",
                endpoint="linkedin/search",
                endpoint_attempts=[
                    {
                        "endpoint": "linkedin/search",
                        "type": "people",
                        "skipped": True,
                        "reason": "no_person_name",
                        "query": request.query[:120],
                    }
                ],
            )

        first_name, last_name = parsed
        params = {
            "api_key": self.settings.scrapingdog_api_key,
            "first_name": first_name,
            "last_name": last_name,
            "type": "people",
        }
        async with get_http_client() as client:
            resp = await client.get(
                "https://api.scrapingdog.com/linkedin/search",
                params=params,
            )
        endpoint_attempts = [
            {
                "endpoint": "linkedin/search",
                "type": "people",
                "status_code": resp.status_code,
                "first_name": first_name,
                "last_name": last_name,
            }
        ]
        if resp.status_code >= 400:
            # Do NOT fall back to Scrapingdog /google — it is expensive.
            detail = resp.text[:300]
            raise ProviderEndpointError(
                f"scrapingdog linkedin/search people failed "
                f"status={resp.status_code} body={detail}",
                endpoint="linkedin/search",
                endpoint_attempts=endpoint_attempts,
            )

        data = resp.json()
        results: list[SearchResultItem] = []
        items = (
            data
            if isinstance(data, list)
            else data.get("people") or data.get("organic_results") or data.get("results") or []
        )
        for item in items[: request.limit]:
            if not isinstance(item, dict):
                continue
            extra = {
                k: v
                for k, v in item.items()
                if k not in {"name", "title", "link", "url", "profile_url"}
            }
            extra["scrapingdog_endpoint"] = "linkedin/search"
            results.append(
                SearchResultItem(
                    title=item.get("name") or item.get("title") or "",
                    url=_clean_url(
                        item.get("link") or item.get("profile_url") or item.get("url") or ""
                    ),
                    snippet=item.get("headline")
                    or item.get("snippet")
                    or item.get("about")
                    or item.get("title")
                    or "",
                    source=self.name,
                    extra=_redact_secrets(extra),
                )
            )
        return ProviderResponse(
            results=results,
            raw={
                "endpoint": "linkedin/search",
                "endpoint_attempts": endpoint_attempts,
                "items": data if isinstance(data, list) else data,
            },
        )


class ScrapingdogCompanyProvider(BaseProvider):
    """Company lookup via Scrapingdog Profile API (not LinkedIn name search).

    Docs: ``GET /profile/?type=company&id=<linkedin_universal_name>``.
    Needs a LinkedIn slug (``amazon``) or company URL — not free-text like
    ``ГК MILLHOUSE``. Expensive (~50 credits); deferred to near end of chain
    (after cheaper company/web providers, before scrapingdog_google).
    """

    name = "scrapingdog_company"
    capability = "company_search"
    priority = 95
    cost = 50

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return bool(self.settings.scrapingdog_api_key.strip())

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        slug = linkedin_company_slug(request.query)
        if not slug:
            raise ProviderEndpointError(
                "scrapingdog profile company skipped: need LinkedIn company slug/URL "
                "(e.g. amazon or linkedin.com/company/amazon), not free-text name search",
                endpoint="profile",
                endpoint_attempts=[
                    {
                        "endpoint": "profile",
                        "type": "company",
                        "skipped": True,
                        "reason": "no_company_slug",
                        "query": request.query[:120],
                    }
                ],
            )

        params = {
            "api_key": self.settings.scrapingdog_api_key,
            "type": "company",
            "id": slug,
        }
        async with get_http_client() as client:
            resp = await client.get("https://api.scrapingdog.com/profile/", params=params)
        endpoint_attempts = [
            {
                "endpoint": "profile",
                "type": "company",
                "id": slug,
                "status_code": resp.status_code,
            }
        ]
        if resp.status_code >= 400:
            detail = resp.text[:300]
            raise ProviderEndpointError(
                f"scrapingdog profile company failed status={resp.status_code} "
                f"id={slug!r} body={detail}",
                endpoint="profile",
                endpoint_attempts=endpoint_attempts,
            )

        data = resp.json()
        items = data if isinstance(data, list) else [data]
        results: list[SearchResultItem] = []
        for item in items[: request.limit]:
            if not isinstance(item, dict):
                continue
            name = item.get("company_name") or item.get("name") or slug
            uid = item.get("universal_name_id") or slug
            url = (
                item.get("company_link")
                or item.get("website")
                or f"https://www.linkedin.com/company/{uid}"
            )
            snippet_parts = [
                item.get("tagline") or "",
                item.get("industry") or "",
                item.get("headquarters") or "",
                item.get("company_size") or "",
            ]
            snippet = " · ".join(p for p in snippet_parts if p) or (item.get("description") or "")[:280]
            extra = {
                k: v
                for k, v in item.items()
                if k
                not in {
                    "company_name",
                    "name",
                    "company_link",
                    "website",
                    "tagline",
                    "description",
                }
            }
            extra["scrapingdog_endpoint"] = "profile"
            results.append(
                SearchResultItem(
                    title=str(name),
                    url=_clean_url(str(url)),
                    snippet=str(snippet),
                    source=self.name,
                    extra=_redact_secrets(extra),
                    content=item.get("description"),
                )
            )
        return ProviderResponse(
            results=results,
            raw={
                "endpoint": "profile",
                "endpoint_attempts": endpoint_attempts,
                "items": data,
            },
        )
