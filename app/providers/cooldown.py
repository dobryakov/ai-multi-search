from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from app.cache.redis import Cache, get_cache
from app.config import Settings, get_settings
from app.providers.errors import ProviderEndpointError

logger = logging.getLogger(__name__)

# Auth / quota / rate-limit of the *provider API* only.
# Do NOT include 403: for scrape providers (trafilatura) that is usually the
# target page, not provider auth — cooling down would disable free scrape globally.
COOLDOWN_STATUSES = {401, 402, 429}
QUOTA_HINTS = re.compile(
    r"(quota|rate.?limit|too many requests|insufficient|credit|payment|billing|"
    r"exceeded|out of|plan limit|unauthorized|subscription)",
    re.I,
)


def cooldown_reason_from_error(exc: BaseException) -> str | None:
    """Return reason string if this error should trigger provider cooldown."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in COOLDOWN_STATUSES:
            return f"http_{status}"
        return None

    # Paid/search API returned 5xx (e.g. Scrapingdog Google HTML 500).
    # Circuit that provider so every person_search does not keep paying a dead hop.
    if isinstance(exc, ProviderEndpointError):
        for attempt in exc.endpoint_attempts or []:
            status = attempt.get("status_code")
            if isinstance(status, int) and status >= 500:
                return f"upstream_{status}"

    text = str(exc)
    status_match = re.search(r"\b(401|402|429)\b", text)
    if status_match:
        return f"http_{status_match.group(1)}"
    if QUOTA_HINTS.search(text):
        return "quota_or_auth_message"
    return None


class ProviderCooldownout:
    """Temporary skip of providers after quota/auth/rate-limit failures."""

    def __init__(self, cache: Cache | None = None, settings: Settings | None = None) -> None:
        self.cache = cache or get_cache()
        self.settings = settings or get_settings()
        self._memory: dict[str, dict[str, Any]] = {}

    def _key(self, provider: str) -> str:
        return f"ams:cooldown:{provider}"

    def _seconds(self) -> int:
        return max(0, int(self.settings.provider_cooldown_seconds))

    async def mark(self, provider: str, reason: str, seconds: int | None = None) -> int:
        ttl = self._seconds() if seconds is None else max(0, seconds)
        if ttl <= 0:
            return 0
        until = time.time() + ttl
        payload = {"reason": reason, "until": until, "seconds": ttl}
        raw = json.dumps(payload)
        if self.cache._redis is not None:
            try:
                await self.cache._redis.set(self._key(provider), raw, ex=ttl)
                logger.info("Provider %s cooldown %ss reason=%s", provider, ttl, reason)
                return ttl
            except Exception as exc:  # noqa: BLE001
                logger.warning("cooldown redis set failed: %s", exc)
        self._memory[provider] = payload
        logger.info("Provider %s cooldown %ss reason=%s (memory)", provider, ttl, reason)
        return ttl

    async def get(self, provider: str) -> dict[str, Any] | None:
        now = time.time()
        if self.cache._redis is not None:
            try:
                raw = await self.cache._redis.get(self._key(provider))
                if raw:
                    data = json.loads(raw)
                    remaining = max(0, int(float(data.get("until", 0)) - now))
                    if remaining <= 0:
                        return None
                    return {
                        "reason": data.get("reason"),
                        "until": data.get("until"),
                        "remaining_seconds": remaining,
                    }
            except Exception as exc:  # noqa: BLE001
                logger.warning("cooldown redis get failed: %s", exc)
        data = self._memory.get(provider)
        if not data:
            return None
        remaining = max(0, int(float(data.get("until", 0)) - now))
        if remaining <= 0:
            self._memory.pop(provider, None)
            return None
        return {
            "reason": data.get("reason"),
            "until": data.get("until"),
            "remaining_seconds": remaining,
        }

    async def is_active(self, provider: str) -> bool:
        return (await self.get(provider)) is not None

    async def clear(self, provider: str) -> None:
        self._memory.pop(provider, None)
        if self.cache._redis is not None:
            try:
                await self.cache._redis.delete(self._key(provider))
            except Exception as exc:  # noqa: BLE001
                logger.warning("cooldown redis delete failed: %s", exc)

    async def active_map(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        if self.cache._redis is not None:
            try:
                async for key in self.cache._redis.scan_iter(match="ams:cooldown:*", count=100):
                    name = key.split("ams:cooldown:", 1)[-1]
                    info = await self.get(name)
                    if info:
                        out[name] = info
                return out
            except Exception as exc:  # noqa: BLE001
                logger.warning("cooldown scan failed: %s", exc)
        for name in list(self._memory.keys()):
            info = await self.get(name)
            if info:
                out[name] = info
        return out


_cooldown: ProviderCooldownout | None = None


def get_provider_cooldown() -> ProviderCooldownout:
    global _cooldown
    if _cooldown is None:
        _cooldown = ProviderCooldownout()
    return _cooldown


def set_provider_cooldown(cooldown: ProviderCooldownout) -> None:
    global _cooldown
    _cooldown = cooldown
