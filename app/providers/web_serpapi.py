from __future__ import annotations

from app.config import Settings, get_settings
from app.normalize.schema import SearchResultItem
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.http_util import get_http_client


class SerpApiProvider(BaseProvider):
    name = "serpapi"
    capability = "web_search"
    priority = 40
    cost = 35

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return bool(self.settings.serpapi_api_key.strip())

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        params: dict[str, str | int] = {
            "engine": "google",
            "q": request.query,
            "api_key": self.settings.serpapi_api_key,
            "num": request.limit,
        }
        if request.country:
            params["gl"] = request.country
        if request.language:
            params["hl"] = request.language
        async with get_http_client() as client:
            resp = await client.get("https://serpapi.com/search", params=params)
            resp.raise_for_status()
            data = resp.json()

        results: list[SearchResultItem] = []
        kg = data.get("knowledge_graph") or {}
        if kg:
            results.append(
                SearchResultItem(
                    title=kg.get("title") or "Knowledge Graph",
                    url=kg.get("website") or kg.get("source", {}).get("link") or "",
                    snippet=kg.get("description") or "",
                    source=self.name,
                    extra={"type": "knowledge_graph"},
                )
            )
        for item in data.get("organic_results", [])[: request.limit]:
            results.append(
                SearchResultItem(
                    title=item.get("title") or "",
                    url=item.get("link") or "",
                    snippet=item.get("snippet") or "",
                    source=self.name,
                )
            )
        return ProviderResponse(results=results[: request.limit], raw=data)


class SerpApiPersonProvider(SerpApiProvider):
    name = "serpapi_person"
    capability = "person_search"
    priority = 50
    cost = 35


class SerpApiCompanyProvider(SerpApiProvider):
    name = "serpapi_company"
    capability = "company_search"
    priority = 50
    cost = 35
