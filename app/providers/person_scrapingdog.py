from __future__ import annotations

from app.config import Settings, get_settings
from app.normalize.schema import SearchResultItem
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.http_util import get_http_client


class ScrapingdogPeopleProvider(BaseProvider):
    name = "scrapingdog_people"
    capability = "person_search"
    priority = 10
    cost = 40

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return bool(self.settings.scrapingdog_api_key.strip())

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        # Scrapingdog LinkedIn people / Google-like people search.
        params = {
            "api_key": self.settings.scrapingdog_api_key,
            "query": request.query,
            "type": "people",
        }
        async with get_http_client() as client:
            resp = await client.get(
                "https://api.scrapingdog.com/linkedin/search",
                params=params,
            )
            if resp.status_code >= 400:
                # Fallback endpoint shape for generic search.
                resp = await client.get(
                    "https://api.scrapingdog.com/google",
                    params={
                        "api_key": self.settings.scrapingdog_api_key,
                        "query": request.query,
                        "results": request.limit,
                    },
                )
            resp.raise_for_status()
            data = resp.json()

        results: list[SearchResultItem] = []
        items = data if isinstance(data, list) else data.get("people") or data.get("organic_results") or data.get("results") or []
        for item in items[: request.limit]:
            if not isinstance(item, dict):
                continue
            results.append(
                SearchResultItem(
                    title=item.get("name") or item.get("title") or "",
                    url=item.get("link") or item.get("profile_url") or item.get("url") or "",
                    snippet=item.get("title")
                    or item.get("headline")
                    or item.get("snippet")
                    or item.get("about")
                    or "",
                    source=self.name,
                    extra={k: v for k, v in item.items() if k not in {"name", "title", "link", "url"}},
                )
            )
        return ProviderResponse(results=results, raw=data if isinstance(data, dict) else {"items": data})


class ScrapingdogCompanyProvider(BaseProvider):
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
            if resp.status_code >= 400:
                resp = await client.get(
                    "https://api.scrapingdog.com/google",
                    params={
                        "api_key": self.settings.scrapingdog_api_key,
                        "query": request.query,
                        "results": request.limit,
                    },
                )
            resp.raise_for_status()
            data = resp.json()

        results: list[SearchResultItem] = []
        items = data if isinstance(data, list) else data.get("companies") or data.get("organic_results") or data.get("results") or []
        for item in items[: request.limit]:
            if not isinstance(item, dict):
                continue
            results.append(
                SearchResultItem(
                    title=item.get("name") or item.get("title") or "",
                    url=item.get("link") or item.get("company_url") or item.get("url") or "",
                    snippet=item.get("about") or item.get("snippet") or item.get("industry") or "",
                    source=self.name,
                    extra={k: v for k, v in item.items() if k not in {"name", "title", "link", "url"}},
                )
            )
        return ProviderResponse(results=results, raw=data if isinstance(data, dict) else {"items": data})
