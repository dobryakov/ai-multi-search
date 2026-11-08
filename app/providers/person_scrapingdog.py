from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

from app.config import Settings, get_settings
from app.normalize.schema import SearchResultItem
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.errors import ProviderEndpointError
from app.providers.http_util import get_http_client

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


def _split_person_name(query: str) -> tuple[str, str]:
    parts = [p for p in query.strip().split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


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
        first_name, last_name = _split_person_name(request.query)
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
            # Raise so ProviderManager continues to Serper/SerpAPI/etc.
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
    """LinkedIn company search only. No expensive Scrapingdog Google fallback."""

    name = "scrapingdog_company"
    capability = "company_search"
    priority = 10
    cost = 40

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return bool(self.settings.scrapingdog_api_key.strip())

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        params = {
            "api_key": self.settings.scrapingdog_api_key,
            "query": request.query,
            "type": "companies",
        }
        async with get_http_client() as client:
            resp = await client.get(
                "https://api.scrapingdog.com/linkedin/search",
                params=params,
            )
        endpoint_attempts = [
            {
                "endpoint": "linkedin/search",
                "type": "companies",
                "status_code": resp.status_code,
            }
        ]
        if resp.status_code >= 400:
            detail = resp.text[:300]
            raise ProviderEndpointError(
                f"scrapingdog linkedin/search companies failed "
                f"status={resp.status_code} body={detail}",
                endpoint="linkedin/search",
                endpoint_attempts=endpoint_attempts,
            )

        data = resp.json()
        results: list[SearchResultItem] = []
        items = (
            data
            if isinstance(data, list)
            else data.get("companies") or data.get("organic_results") or data.get("results") or []
        )
        for item in items[: request.limit]:
            if not isinstance(item, dict):
                continue
            extra = {
                k: v
                for k, v in item.items()
                if k not in {"name", "title", "link", "url", "company_url"}
            }
            extra["scrapingdog_endpoint"] = "linkedin/search"
            results.append(
                SearchResultItem(
                    title=item.get("name") or item.get("title") or "",
                    url=_clean_url(
                        item.get("link") or item.get("company_url") or item.get("url") or ""
                    ),
                    snippet=item.get("about") or item.get("snippet") or item.get("industry") or "",
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
