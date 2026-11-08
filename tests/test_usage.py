from __future__ import annotations

import pytest

from app.cache.redis import Cache
from app.config import Settings
from app.usage.tracker import UsageTracker


@pytest.mark.asyncio
async def test_usage_tracker_memory_counters():
    settings = Settings(redis_url="redis://localhost:9/0")
    cache = Cache(settings)
    # force memory mode
    cache._redis = None
    tracker = UsageTracker(cache=cache, settings=settings)

    await tracker.record_request(capability="web_search", status="ok", cache_hit=False)
    await tracker.record_provider(provider="serper", outcome="ok", latency_ms=12.5)
    await tracker.record_provider(provider="serper", outcome="error", latency_ms=3.0)
    await tracker.record_openrouter(
        ok=True,
        model="typesafe/jev-1.14",
        input_tokens=100,
        output_tokens=10,
        cost=0.00002,
    )

    snap = await tracker.snapshot()
    assert snap["all_time"]["total"]["requests"] == 1
    assert snap["all_time"]["providers"]["serper"]["calls"] == 2
    assert snap["all_time"]["providers"]["serper"]["ok"] == 1
    assert snap["all_time"]["providers"]["serper"]["error"] == 1
    assert snap["all_time"]["openrouter"]["calls"] == 1
    assert snap["all_time"]["openrouter"]["cost_usd"] == pytest.approx(0.00002)
