from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


CapabilityName = Literal[
    "web_search",
    "person_search",
    "company_search",
    "scrape",
    "search_and_scrape",
]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    type: CapabilityName | None = None
    limit: int = Field(default=10, ge=1, le=50)
    language: str | None = None
    country: str | None = None
    scrape: bool = False
    formats: list[str] = Field(default_factory=lambda: ["markdown"])


class ScrapeRequest(BaseModel):
    url: HttpUrl
    formats: list[str] = Field(default_factory=lambda: ["markdown"])


class SearchResultItem(BaseModel):
    title: str = ""
    url: str = ""
    snippet: str = ""
    source: str = ""
    content: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ScrapeResult(BaseModel):
    url: str
    title: str = ""
    markdown: str | None = None
    html: str | None = None
    text: str | None = None
    source: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    capability: CapabilityName
    provider_used: str | None = None
    fallback_chain: list[str] = Field(default_factory=list)
    results: list[SearchResultItem] = Field(default_factory=list)
    scrape_results: list[ScrapeResult] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class ProviderInfo(BaseModel):
    name: str
    capability: CapabilityName
    available: bool
    priority: int
    cost: int


class CapabilityInfo(BaseModel):
    name: CapabilityName
    available: bool
    providers: list[ProviderInfo]


class CapabilitiesResponse(BaseModel):
    capabilities: list[CapabilityInfo]
