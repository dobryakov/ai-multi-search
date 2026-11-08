from __future__ import annotations

from app.config import Settings, get_settings
from app.normalize.schema import SearchResultItem
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.http_util import get_http_client


class SerperProvider(BaseProvider):
    name = "serper"
    capability = "web_search"
    priority = 30
    cost = 25

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return bool(self.settings.resolved_serper_key())

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        key = self.settings.resolved_serper_key()
        payload: dict[str, object] = {
            "q": request.query,
            "num": request.limit,
        }
        if request.country:
            payload["gl"] = request.country
        if request.language:
            payload["hl"] = request.language
        async with get_http_client() as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        results: list[SearchResultItem] = []
        kg = data.get("knowledgeGraph") or {}
        if kg:
            results.append(
                SearchResultItem(
                    title=kg.get("title") or "Knowledge Graph",
                    url=kg.get("website") or kg.get("descriptionLink") or "",
                    snippet=kg.get("description") or "",
                    source=self.name,
                    extra={"type": "knowledgeGraph", "kg": kg},
                )
            )
        answer = data.get("answerBox") or {}
        if answer:
            results.append(
                SearchResultItem(
                    title=answer.get("title") or "Answer",
                    url=answer.get("link") or "",
                    snippet=answer.get("answer") or answer.get("snippet") or "",
                    source=self.name,
                    extra={"type": "answerBox"},
                )
            )
        for item in data.get("organic", [])[: request.limit]:
            results.append(
                SearchResultItem(
                    title=item.get("title") or "",
                    url=item.get("link") or "",
                    snippet=item.get("snippet") or "",
                    source=self.name,
                )
            )
        return ProviderResponse(results=results[: request.limit], raw=data)


class SerperPersonProvider(SerperProvider):
    name = "serper_person"
    capability = "person_search"
    priority = 40
    cost = 25


class SerperCompanyProvider(SerperProvider):
    name = "serper_company"
    capability = "company_search"
    priority = 40
    cost = 25
