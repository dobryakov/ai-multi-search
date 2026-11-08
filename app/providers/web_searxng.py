from __future__ import annotations

from app.config import Settings, get_settings
from app.normalize.schema import SearchResultItem
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.http_util import get_http_client


class SearxngProvider(BaseProvider):
    name = "searxng"
    capability = "web_search"
    priority = 10
    cost = 5

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return bool(self.settings.searxng_url.strip())

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        base = self.settings.searxng_url.rstrip("/")
        params = {
            "q": request.query,
            "format": "json",
            "pageno": 1,
        }
        if request.language:
            params["language"] = request.language
        async with get_http_client() as client:
            resp = await client.get(f"{base}/search", params=params)
            resp.raise_for_status()
            data = resp.json()
        results: list[SearchResultItem] = []
        for item in data.get("results", [])[: request.limit]:
            results.append(
                SearchResultItem(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    snippet=item.get("content") or item.get("snippet") or "",
                    source=self.name,
                )
            )
        return ProviderResponse(results=results, raw=data if isinstance(data, dict) else {})
