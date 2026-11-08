from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class Cache:
    """Redis-backed cache with in-memory fallback."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._redis = None
        self._memory: dict[str, tuple[float, str]] = {}
        self._rate: dict[str, list[float]] = {}

    async def connect(self) -> None:
        try:
            import redis.asyncio as redis

            client = redis.from_url(
                self.settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await client.ping()
            self._redis = client
            logger.info("Redis cache connected")
        except Exception as exc:  # noqa: BLE001
            self._redis = None
            logger.warning("Redis unavailable, using memory cache: %s", exc)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    @staticmethod
    def make_key(capability: str, query: str, params: dict[str, Any]) -> str:
        payload = json.dumps(
            {"c": capability, "q": query, "p": params},
            sort_keys=True,
            ensure_ascii=False,
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()
        return f"ams:cache:{digest}"

    async def get_json(self, key: str) -> dict[str, Any] | None:
        raw: str | None = None
        if self._redis is not None:
            try:
                raw = await self._redis.get(key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Redis get failed: %s", exc)
        if raw is None:
            item = self._memory.get(key)
            if item is None:
                return None
            expires, raw = item
            if expires < time.time():
                self._memory.pop(key, None)
                return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    async def set_json(self, key: str, value: dict[str, Any], ttl: int | None = None) -> None:
        ttl = ttl if ttl is not None else self.settings.cache_ttl_seconds
        raw = json.dumps(value, ensure_ascii=False)
        if self._redis is not None:
            try:
                await self._redis.set(key, raw, ex=ttl)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("Redis set failed: %s", exc)
        self._memory[key] = (time.time() + ttl, raw)

    async def check_rate_limit(self, identity: str) -> bool:
        """Return True if request is allowed."""
        limit = self.settings.rate_limit_per_minute
        if limit <= 0:
            return True
        now = time.time()
        window_key = f"ams:rl:{identity}"
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                pipe.zremrangebyscore(window_key, 0, now - 60)
                pipe.zadd(window_key, {str(now): now})
                pipe.zcard(window_key)
                pipe.expire(window_key, 60)
                results = await pipe.execute()
                count = int(results[2])
                return count <= limit
            except Exception as exc:  # noqa: BLE001
                logger.warning("Redis rate limit failed: %s", exc)

        stamps = [t for t in self._rate.get(identity, []) if t > now - 60]
        stamps.append(now)
        self._rate[identity] = stamps
        return len(stamps) <= limit


_cache: Cache | None = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache


def set_cache(cache: Cache) -> None:
    global _cache
    _cache = cache
