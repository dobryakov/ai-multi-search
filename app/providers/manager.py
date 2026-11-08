from __future__ import annotations

import logging
import time
from typing import Any

from app.capabilities.registry import CapabilityRegistry
from app.config import Settings, get_settings
from app.normalize.schema import CapabilityName, SearchResultItem
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.cooldown import (
    ProviderCooldownout,
    cooldown_reason_from_error,
    get_provider_cooldown,
)
from app.providers.person_scrapingdog import ScrapingdogCompanyProvider, ScrapingdogPeopleProvider
from app.providers.scrape_crawl4ai import Crawl4AIProvider
from app.providers.scrape_trafilatura import TrafilaturaProvider
from app.providers.web_brave import BraveSearchProvider
from app.providers.web_firecrawl import (
    FirecrawlCompanyProvider,
    FirecrawlScrapeProvider,
    FirecrawlSearchProvider,
)
from app.providers.web_scrapingdog_google import ScrapingdogGoogleProvider
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
        cooldown: ProviderCooldownout | None = None,
    ) -> None:
        self.registry = registry
        self.usage = usage or get_usage_tracker()
        self.cooldown = cooldown or get_provider_cooldown()

    async def execute(
        self,
        capability: CapabilityName,
        request: ProviderRequest,
    ) -> tuple[ProviderResponse, str | None, list[str], list[dict[str, Any]]]:
        providers = self.registry.providers_for(capability, only_available=True)
        # Expensive Scrapingdog company Profile API (~50 credits): keep after cheaper
        # specialized + web_search, still before absolute-last scrapingdog_google.
        deferred_company = [p for p in providers if p.name == "scrapingdog_company"]
        providers = [p for p in providers if p.name != "scrapingdog_company"]
        # For person/company: after specialized providers, continue into cheaper web_search
        # providers and only then last-resort scrapingdog_google.
        if capability in {"person_search", "company_search"}:
            seen = {p.name for p in providers}
            for provider in self.registry.providers_for("web_search", only_available=True):
                if provider.name not in seen:
                    providers.append(provider)
                    seen.add(provider.name)
        if deferred_company:
            google_idx = next(
                (i for i, p in enumerate(providers) if p.name == "scrapingdog_google"),
                None,
            )
            if google_idx is None:
                providers.extend(deferred_company)
            else:
                providers[google_idx:google_idx] = deferred_company

        chain: list[str] = []
        attempts: list[dict[str, Any]] = []
        errors: list[str] = []
        for provider in providers:
            cooling = await self.cooldown.get(provider.name)
            if cooling:
                chain.append(provider.name)
                attempts.append(
                    {
                        "provider": provider.name,
                        "outcome": "skipped_cooldown",
                        "latency_ms": 0.0,
                        "cooldown_remaining_seconds": cooling.get("remaining_seconds"),
                        "error": f"cooldown:{cooling.get('reason')}",
                    }
                )
                continue

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
                attempt: dict[str, Any] = {
                    "provider": provider.name,
                    "outcome": outcome,
                    "latency_ms": round(latency_ms, 1),
                }
                if isinstance(response.raw, dict):
                    if response.raw.get("endpoint"):
                        attempt["endpoint"] = response.raw.get("endpoint")
                    if response.raw.get("endpoint_attempts"):
                        attempt["endpoint_attempts"] = response.raw.get("endpoint_attempts")
                attempts.append(attempt)
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
                reason = cooldown_reason_from_error(exc)
                cooldown_remaining = None
                if reason:
                    cooldown_remaining = await self.cooldown.mark(provider.name, reason)
                err_attempt: dict[str, Any] = {
                    "provider": provider.name,
                    "outcome": "error",
                    "latency_ms": round(latency_ms, 1),
                    "error": str(exc)[:300],
                    "cooldown_remaining_seconds": cooldown_remaining or None,
                }
                endpoint = getattr(exc, "endpoint", None)
                endpoint_attempts = getattr(exc, "endpoint_attempts", None)
                if endpoint:
                    err_attempt["endpoint"] = endpoint
                if endpoint_attempts:
                    err_attempt["endpoint_attempts"] = endpoint_attempts
                attempts.append(err_attempt)
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
        # Absolute last resort for web_search (expensive).
        ScrapingdogGoogleProvider(settings),
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
    cooldown: ProviderCooldownout | None = None,
) -> ProviderManager:
    registry = CapabilityRegistry(build_default_providers(settings))
    return ProviderManager(registry, usage=usage, cooldown=cooldown)


def provider_status(registry: CapabilityRegistry) -> dict[str, Any]:
    return registry.describe().model_dump()
