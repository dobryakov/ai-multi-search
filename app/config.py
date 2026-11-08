from __future__ import annotations

import os
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ai-multi-search"
    public_base_url: str = "http://127.0.0.1:18100"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # Comma-separated bearer keys. Empty = auth disabled (local only).
    api_keys: str = ""

    redis_url: str = "redis://redis:6379/0"
    cache_ttl_seconds: int = 900
    rate_limit_per_minute: int = 60

    # OpenRouter / Jev (Decisions API). Prefer OPENROUTER_API_KEY if LLM_API_KEY empty.
    openrouter_api_key: str = ""
    llm_base_url: str = "https://openrouter.ai/api"
    llm_api_key: str = ""
    llm_model: str = "typesafe/jev-1.14"
    intent_confidence_threshold: float = 0.55
    llm_timeout_seconds: float = 12.0

    # Provider keys / URLs
    searxng_url: str = ""
    brave_api_key: str = ""
    serper_api_key: str = ""
    serpapi_api_key: str = ""
    tavily_api_key: str = ""
    firecrawl_api_key: str = ""
    firecrawl_base_url: str = "https://api.firecrawl.dev"
    scrapingdog_api_key: str = ""
    crawl4ai_url: str = ""

    http_timeout_seconds: float = 30.0
    scrape_max_bytes: int = 2_000_000

    @field_validator("api_keys", mode="before")
    @classmethod
    def _strip_keys(cls, value: object) -> object:
        return value if value is not None else ""

    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    def resolved_llm_api_key(self) -> str:
        if self.llm_api_key.strip():
            return self.llm_api_key.strip()
        if self.openrouter_api_key.strip():
            return self.openrouter_api_key.strip()
        return os.getenv("OPENROUTER_API_KEY", "").strip()

    def llm_configured(self) -> bool:
        return bool(self.resolved_llm_api_key())

    def decisions_url(self) -> str:
        base = (self.llm_base_url or "https://openrouter.ai/api").rstrip("/")
        if base.endswith("/alpha/decisions"):
            return base
        if base.endswith("/v1"):
            return base[: -len("/v1")] + "/alpha/decisions"
        if base.endswith("/alpha"):
            return base + "/decisions"
        return base + "/alpha/decisions"

    def resolved_serper_key(self) -> str:
        """Prefer SERPER_API_KEY; accept legacy SERPER_DEV_API_KEY via env alias."""
        if self.serper_api_key:
            return self.serper_api_key
        return os.getenv("SERPER_DEV_API_KEY", "").strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
