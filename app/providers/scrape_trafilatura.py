from __future__ import annotations

from app.config import Settings, get_settings
from app.normalize.schema import ScrapeResult
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.http_util import get_http_client, is_safe_public_url


class TrafilaturaProvider(BaseProvider):
    name = "trafilatura"
    capability = "scrape"
    priority = 10
    cost = 5

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return True

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        if not is_safe_public_url(request.url):
            raise ValueError("URL blocked by SSRF protection")

        async with get_http_client() as client:
            resp = await client.get(request.url)
            resp.raise_for_status()
            html = resp.text
            if len(html.encode("utf-8", errors="ignore")) > self.settings.scrape_max_bytes:
                html = html[: self.settings.scrape_max_bytes]

        import trafilatura

        markdown = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            output_format="markdown",
            url=request.url,
        )
        text = trafilatura.extract(html, include_comments=False, url=request.url)
        meta = trafilatura.extract_metadata(html)
        title = meta.title if meta and meta.title else ""
        want_html = "html" in (request.formats or [])
        scrape = ScrapeResult(
            url=request.url,
            title=title or "",
            markdown=markdown if "markdown" in (request.formats or ["markdown"]) else None,
            html=html if want_html else None,
            text=text,
            source=self.name,
        )
        return ProviderResponse(scrape_results=[scrape])
