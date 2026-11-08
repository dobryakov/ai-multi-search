from __future__ import annotations

from collections import defaultdict

from app.normalize.schema import (
    CapabilitiesResponse,
    CapabilityInfo,
    CapabilityName,
    ProviderInfo,
)
from app.providers.base import BaseProvider


class CapabilityRegistry:
    def __init__(self, providers: list[BaseProvider] | None = None) -> None:
        self._providers: list[BaseProvider] = list(providers or [])

    def register(self, provider: BaseProvider) -> None:
        self._providers.append(provider)

    def all_providers(self) -> list[BaseProvider]:
        return list(self._providers)

    def providers_for(self, capability: CapabilityName, *, only_available: bool = True) -> list[BaseProvider]:
        items = [p for p in self._providers if p.capability == capability]
        if only_available:
            items = [p for p in items if p.available()]
        items.sort(key=lambda p: (p.priority, p.cost, p.name))
        return items

    def describe(self) -> CapabilitiesResponse:
        by_cap: dict[CapabilityName, list[BaseProvider]] = defaultdict(list)
        for provider in self._providers:
            by_cap[provider.capability].append(provider)

        capabilities: list[CapabilityInfo] = []
        for name in (
            "web_search",
            "person_search",
            "company_search",
            "scrape",
            "search_and_scrape",
        ):
            providers = by_cap.get(name, [])  # type: ignore[arg-type]
            infos = [
                ProviderInfo(
                    name=p.name,
                    capability=p.capability,
                    available=p.available(),
                    priority=p.priority,
                    cost=p.cost,
                )
                for p in sorted(providers, key=lambda x: (x.priority, x.cost, x.name))
            ]
            capabilities.append(
                CapabilityInfo(
                    name=name,  # type: ignore[arg-type]
                    available=any(i.available for i in infos),
                    providers=infos,
                )
            )
        return CapabilitiesResponse(capabilities=capabilities)
