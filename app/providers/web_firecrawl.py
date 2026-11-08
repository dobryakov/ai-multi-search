from __future__ import annotations

from app.config import Settings, get_settings
from app.normalize.schema import ScrapeResult, SearchResultItem
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.http_util import get_http_client


class FirecrawlSearchProvider(BaseProvider):
    name = "firecrawl_search"
    capability = "web_search"
    priority = 60
    cost = 40

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return bool(self.settings.firecrawl_api_key.strip())

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        base = self.settings.firecrawl_base_url.rstrip("/")
        payload = {"query": request.query, "limit": request.limit}
        async with get_http_client() as client:
            resp = await client.post(
                f"{base}/v1/search",
                headers={
                    "Authorization": f"Bearer {self.settings.firecrawl_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        items = data.get("data") or data.get("web") or []
        if isinstance(items, dict):
            items = items.get("results") or []
        results = [
            SearchResultItem(
                title=item.get("title") or "",
                url=item.get("url") or "",
                snippet=item.get("description") or item.get("markdown") or "",
                source=self.name,
            )
            for item in items[: request.limit]
            if isinstance(item, dict)
        ]
        return ProviderResponse(results=results, raw=data if isinstance(data, dict) else {})


class FirecrawlScrapeProvider(BaseProvider):
    name = "firecrawl_scrape"
    capability = "scrape"
    priority = 40
    cost = 40

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return bool(self.settings.firecrawl_api_key.strip())

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        base = self.settings.firecrawl_base_url.rstrip("/")
        payload = {
            "url": request.url,
            "formats": request.formats or ["markdown"],
        }
        async with get_http_client() as client:
            resp = await client.post(
                f"{base}/v1/scrape",
                headers={
                    "Authorization": f"Bearer {self.settings.firecrawl_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        body = data.get("data") or data
        scrape = ScrapeResult(
            url=request.url,
            title=(body.get("metadata") or {}).get("title") or "",
            markdown=body.get("markdown"),
            html=body.get("html"),
            text=body.get("content") or body.get("text"),
            source=self.name,
        )
        return ProviderResponse(scrape_results=[scrape], raw=data if isinstance(data, dict) else {})


class FirecrawlCompanyProvider(FirecrawlSearchProvider):
    name = "firecrawl_company"
    capability = "company_search"
    priority = 30
    cost = 40
