from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.normalize.schema import CapabilityName, ScrapeResult, SearchResultItem


@dataclass
class ProviderRequest:
    query: str = ""
    url: str = ""
    limit: int = 10
    language: str | None = None
    country: str | None = None
    formats: list[str] = field(default_factory=lambda: ["markdown"])
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderResponse:
    results: list[SearchResultItem] = field(default_factory=list)
    scrape_results: list[ScrapeResult] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class BaseProvider(ABC):
    name: str
    capability: CapabilityName
    # Lower is tried earlier among available providers.
    priority: int = 100
    # Relative cost units (lower preferred when priority ties).
    cost: int = 50

    def available(self) -> bool:
        return True

    async def health(self) -> bool:
        return self.available()

    @abstractmethod
    async def run(self, request: ProviderRequest) -> ProviderResponse:
        raise NotImplementedError
