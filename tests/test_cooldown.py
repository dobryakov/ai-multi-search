from __future__ import annotations

import httpx
import pytest

from app.cache.redis import Cache
from app.config import Settings
from app.normalize.schema import SearchResultItem
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.cooldown import ProviderCooldownout, cooldown_reason_from_error
from app.capabilities.registry import CapabilityRegistry
from app.providers.manager import ProviderManager
from app.usage.tracker import UsageTracker


def test_cooldown_reason_http_status():
    req = httpx.Request("GET", "https://example.com")
    resp = httpx.Response(429, request=req)
    exc = httpx.HTTPStatusError("rate limited", request=req, response=resp)
    assert cooldown_reason_from_error(exc) == "http_429"


def test_cooldown_ignores_target_page_403():
    """Trafilatura 403 is the scraped site, not provider auth — must not cool down."""
    req = httpx.Request("GET", "https://tld-list.com/")
    resp = httpx.Response(403, request=req)
    exc = httpx.HTTPStatusError("Forbidden", request=req, response=resp)
    assert cooldown_reason_from_error(exc) is None
    assert cooldown_reason_from_error(RuntimeError("Client error '403 Forbidden'")) is None


def test_cooldown_reason_upstream_5xx_endpoint_error():
    from app.providers.errors import ProviderEndpointError

    exc = ProviderEndpointError(
        "scrapingdog google people failed status=500",
        endpoint="google",
        endpoint_attempts=[{"endpoint": "google", "type": "people_linkedin", "status_code": 500}],
    )
    assert cooldown_reason_from_error(exc) == "upstream_500"


def test_cooldown_reason_quota_text():
    assert cooldown_reason_from_error(RuntimeError("Insufficient credits")) == "quota_or_auth_message"
    assert cooldown_reason_from_error(RuntimeError("random timeout")) is None


class BoomProvider(BaseProvider):
    name = "boom"
    capability = "web_search"
    priority = 1
    cost = 1

    def available(self) -> bool:
        return True

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        req = httpx.Request("GET", "https://example.com")
        resp = httpx.Response(402, request=req)
        raise httpx.HTTPStatusError("Payment Required", request=req, response=resp)


class OkProvider(BaseProvider):
    name = "ok"
    capability = "web_search"
    priority = 2
    cost = 1

    def available(self) -> bool:
        return True

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            results=[SearchResultItem(title="t", url="https://x", snippet="s", source="ok")]
        )


@pytest.mark.asyncio
async def test_manager_sets_cooldown_and_skips_next_time():
    settings = Settings(provider_cooldown_seconds=600, redis_url="redis://localhost:9/0")
    cache = Cache(settings)
    cache._redis = None
    usage = UsageTracker(cache=cache, settings=settings)
    cooldown = ProviderCooldownout(cache=cache, settings=settings)
    manager = ProviderManager(
        CapabilityRegistry([BoomProvider(), OkProvider()]),
        usage=usage,
        cooldown=cooldown,
    )

    _, used, _, attempts = await manager.execute("web_search", ProviderRequest(query="q", limit=3))
    assert used == "ok"
    assert attempts[0]["outcome"] == "error"
    assert attempts[0]["cooldown_remaining_seconds"] == 600
    assert await cooldown.is_active("boom")

    _, used2, _, attempts2 = await manager.execute("web_search", ProviderRequest(query="q", limit=3))
    assert used2 == "ok"
    assert attempts2[0]["outcome"] == "skipped_cooldown"
    assert attempts2[0]["provider"] == "boom"
    assert attempts2[1]["provider"] == "ok"
