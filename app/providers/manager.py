from __future__ import annotations

import logging
import time
from typing import Any

from app.capabilities.registry import CapabilityRegistry
from app.config import Settings, get_settings
from app.normalize.schema import CapabilityName, SearchResultItem
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.person_scrapingdog import ScrapingdogCompanyProvider, ScrapingdogPeopleProvider
from app.providers.scrape_crawl4ai import Crawl4AIProvider
from app.providers.scrape_trafilatura import TrafilaturaProvider
from app.providers.web_brave import BraveSearchProvider
from app.providers.web_firecrawl import (
    FirecrawlCompanyProvider,
    FirecrawlScrapeProvider,
    FirecrawlSearchProvider,
)
from app.providers.web_searxng import SearxngProvider
from app.providers.web_serpapi import SerpApiCompanyProvider, SerpApiPersonProvider, SerpApiProvider
from app.providers.web_serper import SerperCompanyProvider, SerperPersonProvider, SerperProvider
from app.providers.web_tavily import TavilyProvider
from app.usage.tracker import UsageTracker, get_usage_tracker

logger = logging.getLogger(__name__)


def dedupe_results(results: list[SearchResultItem]) -> list[SearchResultItem]:
    seen: set[str] = set()
    out: list[SearchResultItem] = []
    for item in results:
        key = (item.url or item.title).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


class ProviderManager:
    def __init__(
        self,
        registry: CapabilityRegistry,
        usage: UsageTracker | None = None,
    ) -> None:
        self.registry = registry
        self.usage = usage or get_usage_tracker()

    async def execute(
        self,
        capability: CapabilityName,
        request: ProviderRequest,
    ) -> tuple[ProviderResponse, str | None, list[str], list[dict[str, Any]]]:
        providers = self.registry.providers_for(capability, only_available=True)
        if not providers and capability in {"person_search", "company_search"}:
            providers = self.registry.providers_for("web_search", only_available=True)

        chain: list[str] = []
        attempts: list[dict[str, Any]] = []
        errors: list[str] = []
        for provider in providers:
            chain.append(provider.name)
            started = time.perf_counter()
            try:
                response = await provider.run(request)
                latency_ms = (time.perf_counter() - started) * 1000
                if capability == "scrape":
                    ok = bool(response.scrape_results)
                else:
                    ok = bool(response.results)
                outcome = "ok" if ok else "empty"
                await self.usage.record_provider(
                    provider=provider.name,
                    outcome=outcome,
                    latency_ms=latency_ms,
                )
                attempts.append(
                    {
                        "provider": provider.name,
                        "outcome": outcome,
                        "latency_ms": round(latency_ms, 1),
                    }
                )
                if ok:
                    if capability != "scrape":
                        response.results = dedupe_results(response.results)[: request.limit]
                    return response, provider.name, chain, attempts
                errors.append(f"{provider.name}: empty")
            except Exception as exc:  # noqa: BLE001
                latency_ms = (time.perf_counter() - started) * 1000
                logger.warning("Provider %s failed: %s", provider.name, exc)
                await self.usage.record_provider(
                    provider=provider.name,
                    outcome="error",
                    latency_ms=latency_ms,
                )
                attempts.append(
                    {
                        "provider": provider.name,
                        "outcome": "error",
                        "latency_ms": round(latency_ms, 1),
                        "error": str(exc)[:300],
                    }
                )
                errors.append(f"{provider.name}: {exc}")

        empty = ProviderResponse()
        empty.raw = {"errors": errors}
        return empty, None, chain, attempts


def build_default_providers(settings: Settings | None = None) -> list[BaseProvider]:
    settings = settings or get_settings()
    return [
        SearxngProvider(settings),
        BraveSearchProvider(settings),
        SerperProvider(settings),
        SerpApiProvider(settings),
        TavilyProvider(settings),
        FirecrawlSearchProvider(settings),
        ScrapingdogPeopleProvider(settings),
        SerperPersonProvider(settings),
        SerpApiPersonProvider(settings),
        ScrapingdogCompanyProvider(settings),
        FirecrawlCompanyProvider(settings),
        SerperCompanyProvider(settings),
        SerpApiCompanyProvider(settings),
        TrafilaturaProvider(settings),
        Crawl4AIProvider(settings),
        FirecrawlScrapeProvider(settings),
    ]


def build_default_manager(
    settings: Settings | None = None,
    usage: UsageTracker | None = None,
) -> ProviderManager:
    registry = CapabilityRegistry(build_default_providers(settings))
    return ProviderManager(registry, usage=usage)


def provider_status(registry: CapabilityRegistry) -> dict[str, Any]:
    return registry.describe().model_dump()
