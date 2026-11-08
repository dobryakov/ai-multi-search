from __future__ import annotations

import pytest

from app.capabilities.registry import CapabilityRegistry
from app.normalize.schema import SearchResultItem
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.manager import ProviderManager, dedupe_results


class FailProvider(BaseProvider):
    name = "fail"
    capability = "web_search"
    priority = 1
    cost = 1

    def available(self) -> bool:
        return True

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        raise RuntimeError("boom")


class OkProvider(BaseProvider):
    name = "ok"
    capability = "web_search"
    priority = 2
    cost = 1

    def available(self) -> bool:
        return True

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            results=[
                SearchResultItem(title="A", url="https://a.example", snippet="x", source="ok"),
                SearchResultItem(title="A dup", url="https://a.example", snippet="y", source="ok"),
            ]
        )


class MissingKeyProvider(BaseProvider):
    name = "missing"
    capability = "web_search"
    priority = 0
    cost = 1

    def available(self) -> bool:
        return False

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        raise AssertionError("should not run")


@pytest.mark.asyncio
async def test_fallback_chain_skips_unavailable_and_failures():
    registry = CapabilityRegistry([MissingKeyProvider(), FailProvider(), OkProvider()])
    manager = ProviderManager(registry)
    response, used, chain, attempts = await manager.execute(
        "web_search",
        ProviderRequest(query="q", limit=10),
    )
    assert used == "ok"
    assert chain == ["fail", "ok"]
    assert len(response.results) == 1
    assert response.results[0].url == "https://a.example"
    assert [a["outcome"] for a in attempts] == ["error", "ok"]


def test_dedupe_results():
    items = [
        SearchResultItem(title="1", url="https://x", snippet=""),
        SearchResultItem(title="2", url="https://x", snippet=""),
        SearchResultItem(title="3", url="https://y", snippet=""),
    ]
    out = dedupe_results(items)
    assert [i.url for i in out] == ["https://x", "https://y"]


def test_capabilities_availability():
    registry = CapabilityRegistry([MissingKeyProvider(), OkProvider()])
    desc = registry.describe()
    web = next(c for c in desc.capabilities if c.name == "web_search")
    assert web.available is True
    names = {p.name: p.available for p in web.providers}
    assert names["missing"] is False
    assert names["ok"] is True


class _CompanyCheap(BaseProvider):
    name = "serper_company"
    capability = "company_search"
    priority = 40
    cost = 25

    def available(self) -> bool:
        return True

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        raise RuntimeError("cheap failed")


class _ScrapingdogCompany(BaseProvider):
    name = "scrapingdog_company"
    capability = "company_search"
    priority = 10  # high in registry sort; manager must still defer it
    cost = 50

    def available(self) -> bool:
        return True

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            results=[SearchResultItem(title="C", url="https://c.example", snippet="", source=self.name)]
        )


class _WebCheap(BaseProvider):
    name = "searxng"
    capability = "web_search"
    priority = 10
    cost = 5

    def available(self) -> bool:
        return True

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        raise RuntimeError("web failed")


class _GoogleLast(BaseProvider):
    name = "scrapingdog_google"
    capability = "web_search"
    priority = 100
    cost = 100

    def available(self) -> bool:
        return True

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        raise RuntimeError("google should not run")


@pytest.mark.asyncio
async def test_scrapingdog_company_deferred_before_google():
    registry = CapabilityRegistry(
        [_ScrapingdogCompany(), _CompanyCheap(), _WebCheap(), _GoogleLast()]
    )
    manager = ProviderManager(registry)
    response, used, chain, _attempts = await manager.execute(
        "company_search",
        ProviderRequest(query="amazon", limit=5),
    )
    assert used == "scrapingdog_company"
    assert chain == ["serper_company", "searxng", "scrapingdog_company"]
    assert response.results[0].source == "scrapingdog_company"
