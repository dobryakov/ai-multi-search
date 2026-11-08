from __future__ import annotations

from app.config import Settings, get_settings
from app.normalize.schema import SearchResultItem
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.http_util import get_http_client


class BraveSearchProvider(BaseProvider):
    name = "brave"
    capability = "web_search"
    priority = 20
    cost = 20

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return bool(self.settings.brave_api_key.strip())

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.settings.brave_api_key,
        }
        params: dict[str, str | int] = {
            "q": request.query,
            "count": request.limit,
        }
        if request.country:
            params["country"] = request.country
        if request.language:
            params["search_lang"] = request.language
        async with get_http_client() as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
        results: list[SearchResultItem] = []
        for item in (data.get("web") or {}).get("results", [])[: request.limit]:
            results.append(
                SearchResultItem(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    snippet=item.get("description") or "",
                    source=self.name,
                )
            )
        return ProviderResponse(results=results, raw=data)
