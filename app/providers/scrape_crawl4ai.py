from __future__ import annotations

from app.config import Settings, get_settings
from app.normalize.schema import ScrapeResult
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.http_util import get_http_client, is_safe_public_url


class Crawl4AIProvider(BaseProvider):
    name = "crawl4ai"
    capability = "scrape"
    priority = 20
    cost = 15

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return bool(self.settings.crawl4ai_url.strip())

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        if not is_safe_public_url(request.url):
            raise ValueError("URL blocked by SSRF protection")
        base = self.settings.crawl4ai_url.rstrip("/")
        payload = {"urls": [request.url], "priority": 10}
        async with get_http_client() as client:
            resp = await client.post(f"{base}/crawl", json=payload)
            resp.raise_for_status()
            data = resp.json()
        results = data.get("results") or data.get("data") or []
        item = results[0] if results else data
        scrape = ScrapeResult(
            url=request.url,
            title=(item.get("metadata") or {}).get("title") or item.get("title") or "",
            markdown=item.get("markdown") or (item.get("markdown_v2") or {}).get("raw_markdown"),
            html=item.get("html"),
            text=item.get("cleaned_html") or item.get("text"),
            source=self.name,
        )
        return ProviderResponse(scrape_results=[scrape], raw=data if isinstance(data, dict) else {})
