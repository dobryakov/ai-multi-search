from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

from app.config import Settings, get_settings
from app.normalize.schema import SearchResultItem
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.http_util import get_http_client

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


class ScrapingdogGoogleProvider(BaseProvider):
    """Expensive last-resort Google SERP via Scrapingdog.

    Keep priority/cost highest so cheaper providers are always tried first.
    """

    name = "scrapingdog_google"
    capability = "web_search"
    priority = 100
    cost = 100

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return bool(self.settings.scrapingdog_api_key.strip())

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        params = {
            "api_key": self.settings.scrapingdog_api_key,
            "query": request.query,
            "results": request.limit,
        }
        async with get_http_client() as client:
            resp = await client.get("https://api.scrapingdog.com/google", params=params)
            resp.raise_for_status()
            data = resp.json()

        items = (
            data
            if isinstance(data, list)
            else data.get("organic_results") or data.get("results") or []
        )
        results: list[SearchResultItem] = []
        for item in items[: request.limit]:
            if not isinstance(item, dict):
                continue
            extra = {
                k: v
                for k, v in item.items()
                if k not in {"title", "link", "url", "snippet"}
            }
            extra["scrapingdog_endpoint"] = "google"
            results.append(
                SearchResultItem(
                    title=item.get("title") or "",
                    url=_clean_url(item.get("link") or item.get("url") or ""),
                    snippet=item.get("snippet") or "",
                    source=self.name,
                    extra=_redact_secrets(extra),
                )
            )
        return ProviderResponse(
            results=results,
            raw={
                "endpoint": "google",
                "endpoint_attempts": [
                    {"endpoint": "google", "type": None, "status_code": resp.status_code}
                ],
                "items": data if isinstance(data, list) else data,
            },
        )
