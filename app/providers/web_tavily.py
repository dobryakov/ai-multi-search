from __future__ import annotations

from app.config import Settings, get_settings
from app.normalize.schema import SearchResultItem
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.http_util import get_http_client


class TavilyProvider(BaseProvider):
    name = "tavily"
    capability = "web_search"
    priority = 50
    cost = 30

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return bool(self.settings.tavily_api_key.strip())

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        payload = {
            "api_key": self.settings.tavily_api_key,
            "query": request.query,
            "max_results": request.limit,
            "include_answer": False,
        }
        async with get_http_client() as client:
            resp = await client.post("https://api.tavily.com/search", json=payload)
            resp.raise_for_status()
            data = resp.json()
        results = [
            SearchResultItem(
                title=item.get("title") or "",
                url=item.get("url") or "",
                snippet=item.get("content") or "",
                source=self.name,
            )
            for item in data.get("results", [])[: request.limit]
        ]
        return ProviderResponse(results=results, raw=data)
