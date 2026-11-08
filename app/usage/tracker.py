from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.cache.redis import Cache, get_cache
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class UsageTracker:
    """Lightweight usage counters in Redis (memory fallback)."""

    def __init__(self, cache: Cache | None = None, settings: Settings | None = None) -> None:
        self.cache = cache or get_cache()
        self.settings = settings or get_settings()
        self._mem: dict[str, float] = {}

    async def _hincr(self, key: str, field: str, amount: float = 1.0) -> None:
        if self.cache._redis is not None:
            try:
                if isinstance(amount, float) and not amount.is_integer():
                    await self.cache._redis.hincrbyfloat(key, field, amount)
                else:
                    await self.cache._redis.hincrby(key, field, int(amount))
                # keep daily keys around ~90 days
                if ":day:" in key:
                    await self.cache._redis.expire(key, 90 * 24 * 3600)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("usage redis incr failed: %s", exc)
        mem_key = f"{key}:{field}"
        self._mem[mem_key] = self._mem.get(mem_key, 0.0) + float(amount)

    async def _hgetall(self, key: str) -> dict[str, float]:
        if self.cache._redis is not None:
            try:
                raw = await self.cache._redis.hgetall(key)
                return {k: float(v) for k, v in raw.items()}
            except Exception as exc:  # noqa: BLE001
                logger.warning("usage redis read failed: %s", exc)
        prefix = f"{key}:"
        return {
            k[len(prefix) :]: v
            for k, v in self._mem.items()
            if k.startswith(prefix)
        }

    async def record_request(
        self,
        *,
        capability: str,
        status: str,
        cache_hit: bool = False,
    ) -> None:
        day = _day()
        await self._hincr("ams:usage:total", "requests")
        await self._hincr(f"ams:usage:day:{day}:total", "requests")
        await self._hincr(f"ams:usage:capability:{capability}", "requests")
        await self._hincr(f"ams:usage:day:{day}:capability:{capability}", "requests")
        await self._hincr("ams:usage:total", status)
        await self._hincr(f"ams:usage:day:{day}:total", status)
        if cache_hit:
            await self._hincr("ams:usage:total", "cache_hits")
            await self._hincr(f"ams:usage:day:{day}:total", "cache_hits")

    async def record_provider(
        self,
        *,
        provider: str,
        outcome: str,
        latency_ms: float = 0.0,
    ) -> None:
        """outcome: ok | empty | error"""
        day = _day()
        for key in (
            f"ams:usage:provider:{provider}",
            f"ams:usage:day:{day}:provider:{provider}",
        ):
            await self._hincr(key, "calls")
            await self._hincr(key, outcome)
            if latency_ms:
                await self._hincr(key, "latency_ms_sum", float(latency_ms))

    async def record_openrouter(
        self,
        *,
        ok: bool,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost: float = 0.0,
    ) -> None:
        day = _day()
        for key in ("ams:usage:openrouter", f"ams:usage:day:{day}:openrouter"):
            await self._hincr(key, "calls")
            await self._hincr(key, "ok" if ok else "error")
            if input_tokens:
                await self._hincr(key, "input_tokens", float(input_tokens))
            if output_tokens:
                await self._hincr(key, "output_tokens", float(output_tokens))
            if cost:
                await self._hincr(key, "cost_usd", float(cost))
            if model:
                await self._hincr(key, f"model:{model}", 1.0)

    async def snapshot(self) -> dict[str, Any]:
        day = _day()
        total = await self._hgetall("ams:usage:total")
        openrouter = await self._hgetall("ams:usage:openrouter")
        day_total = await self._hgetall(f"ams:usage:day:{day}:total")
        day_openrouter = await self._hgetall(f"ams:usage:day:{day}:openrouter")

        providers: dict[str, dict[str, float]] = {}
        day_providers: dict[str, dict[str, float]] = {}
        if self.cache._redis is not None:
            try:
                async for key in self.cache._redis.scan_iter(match="ams:usage:provider:*", count=100):
                    name = key.split("ams:usage:provider:", 1)[-1]
                    providers[name] = await self._hgetall(key)
                async for key in self.cache._redis.scan_iter(
                    match=f"ams:usage:day:{day}:provider:*", count=100
                ):
                    name = key.split(f"ams:usage:day:{day}:provider:", 1)[-1]
                    day_providers[name] = await self._hgetall(key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("usage scan failed: %s", exc)
        else:
            for mem_key, value in self._mem.items():
                if mem_key.startswith("ams:usage:provider:"):
                    rest = mem_key[len("ams:usage:provider:") :]
                    name, _, field = rest.partition(":")
                    providers.setdefault(name, {})[field] = value
                prefix = f"ams:usage:day:{day}:provider:"
                if mem_key.startswith(prefix):
                    rest = mem_key[len(prefix) :]
                    name, _, field = rest.partition(":")
                    day_providers.setdefault(name, {})[field] = value

        capabilities: dict[str, dict[str, float]] = {}
        if self.cache._redis is not None:
            try:
                async for key in self.cache._redis.scan_iter(match="ams:usage:capability:*", count=50):
                    name = key.split("ams:usage:capability:", 1)[-1]
                    capabilities[name] = await self._hgetall(key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("usage capability scan failed: %s", exc)

        return {
            "day": day,
            "all_time": {
                "total": total,
                "openrouter": openrouter,
                "providers": providers,
                "capabilities": capabilities,
            },
            "today": {
                "total": day_total,
                "openrouter": day_openrouter,
                "providers": day_providers,
            },
        }


_tracker: UsageTracker | None = None


def get_usage_tracker() -> UsageTracker:
    global _tracker
    if _tracker is None:
        _tracker = UsageTracker()
    return _tracker


def set_usage_tracker(tracker: UsageTracker) -> None:
    global _tracker
    _tracker = tracker
