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
    # None = auto (on for person_search/company_search). True/False force.
    structured: bool | None = None


class ScrapeRequest(BaseModel):
    url: HttpUrl
    formats: list[str] = Field(default_factory=lambda: ["markdown"])


class LinkedInProfileRequest(BaseModel):
    """Forced Scrapingdog LinkedIn Profile API (no SERP / fallback).

    Provide exactly one of ``id`` (vanity slug) or ``url`` (LinkedIn /in or /company).
    """

    type: Literal["person", "company"]
    id: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=500)
    # None → Settings.scrapingdog_linkedin_premium (default True).
    premium: bool | None = None


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


class ProviderAttemptUsage(BaseModel):
    provider: str
    outcome: Literal["ok", "empty", "error", "skipped_cooldown"]
    latency_ms: float = 0.0
    error: str | None = None
    cooldown_remaining_seconds: int | None = None
    endpoint: str | None = None
    endpoint_attempts: list[dict[str, Any]] | None = None


class OpenRouterRequestUsage(BaseModel):
    model: str | None = None
    ok: bool = True
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    id: str | None = None
    error: str | None = None
    purpose: str | None = None
    parts: list[dict[str, Any]] | None = None


class RequestUsage(BaseModel):
    """Per-request spend / provider activity (this call only)."""

    cache_hit: bool = False
    latency_ms: float = 0.0
    provider_used: str | None = None
    providers: list[ProviderAttemptUsage] = Field(default_factory=list)
    openrouter: OpenRouterRequestUsage | None = None
    cost_usd: float = 0.0
    notes: str | None = (
        "cost_usd is billed USD known for this request (OpenRouter/Jev). "
        "Search/scrape providers are counted in providers[] but rarely expose per-call USD."
    )


class LinkedInProfileResponse(BaseModel):
    type: Literal["person", "company"]
    id: str
    url: str
    provider_used: str = "scrapingdog_profile"
    premium: bool = True
    profile: Any
    usage: RequestUsage = Field(default_factory=RequestUsage)
    meta: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    capability: CapabilityName
    provider_used: str | None = None
    fallback_chain: list[str] = Field(default_factory=list)
    results: list[SearchResultItem] = Field(default_factory=list)
    scrape_results: list[ScrapeResult] = Field(default_factory=list)
    # LLM-normalized entity object (person/company/web); null when skipped/failed.
    structured: dict[str, Any] | None = None
    usage: RequestUsage = Field(default_factory=RequestUsage)
    meta: dict[str, Any] = Field(default_factory=dict)


class ProviderInfo(BaseModel):
    name: str
    capability: CapabilityName
    available: bool
    priority: int
    cost: int
    cooldown: bool = False
    cooldown_remaining_seconds: int = 0
    cooldown_reason: str | None = None


class CapabilityInfo(BaseModel):
    name: CapabilityName
    available: bool
    providers: list[ProviderInfo]


class CapabilitiesResponse(BaseModel):
    capabilities: list[CapabilityInfo]
