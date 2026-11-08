from __future__ import annotations

import logging
import time
from typing import Any

from app.cache.redis import Cache, get_cache
from app.config import Settings, get_settings
from app.intent.router import IntentRouter
from app.normalize.schema import (
    CapabilitiesResponse,
    CapabilityName,
    LinkedInProfileRequest,
    LinkedInProfileResponse,
    OpenRouterRequestUsage,
    ProviderAttemptUsage,
    RequestUsage,
    ScrapeRequest,
    ScrapeResult,
    SearchRequest,
    SearchResponse,
)
from app.providers.base import ProviderRequest
from app.providers.cooldown import (
    ProviderCooldownout,
    cooldown_reason_from_error,
    get_provider_cooldown,
)
from app.providers.errors import ProviderEndpointError
from app.providers.manager import ProviderManager, build_default_manager
from app.providers.scrapingdog_profile import (
    COOLDOWN_ALIASES,
    PROVIDER_NAME as SCRAPINGDOG_PROFILE,
    fetch_scrapingdog_profile,
    resolve_premium,
    resolve_profile_id,
    unwrap_profile_payload,
)
from app.structure.extractor import (
    StructureExtractor,
    merge_openrouter_usage,
    want_structured,
)
from app.usage.tracker import UsageTracker, get_usage_tracker

logger = logging.getLogger(__name__)


class SearchService:
    def __init__(
        self,
        manager: ProviderManager | None = None,
        intent: IntentRouter | None = None,
        cache: Cache | None = None,
        usage: UsageTracker | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.usage = usage or get_usage_tracker()
        if manager is None:
            self.cooldown = get_provider_cooldown()
            self.manager = build_default_manager(
                self.settings, usage=self.usage, cooldown=self.cooldown
            )
        else:
            self.manager = manager
            self.cooldown = manager.cooldown
        self.intent = intent or IntentRouter(self.settings)
        self.cache = cache or get_cache()
        self.structure = StructureExtractor(self.settings)

    async def capabilities(self) -> CapabilitiesResponse:
        desc = self.manager.registry.describe()
        cooling = await self.cooldown.active_map()
        for cap in desc.capabilities:
            for provider in cap.providers:
                info = cooling.get(provider.name)
                if not info:
                    continue
                provider.cooldown = True
                provider.cooldown_remaining_seconds = int(info.get("remaining_seconds") or 0)
                provider.cooldown_reason = info.get("reason")
                provider.available = False
            # Capability is live if any configured provider is not cooling down.
            cap.available = any(p.available for p in cap.providers)
        return desc

    async def usage_snapshot(self) -> dict[str, Any]:
        return await self.usage.snapshot()

    async def _record_openrouter(self, usage: dict[str, Any] | None) -> None:
        if not usage:
            return
        await self.usage.record_openrouter(
            ok=bool(usage.get("ok", True)),
            model=str(usage.get("model") or self.settings.llm_model),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cost=float(usage.get("cost_usd") or 0.0),
        )

    @staticmethod
    def _openrouter_block(raw: dict[str, Any] | None) -> OpenRouterRequestUsage | None:
        if not raw:
            return None
        parts = raw.get("parts")
        return OpenRouterRequestUsage(
            model=raw.get("model"),
            ok=bool(raw.get("ok", True)),
            input_tokens=int(raw.get("input_tokens") or 0),
            output_tokens=int(raw.get("output_tokens") or 0),
            cost_usd=float(raw.get("cost_usd") or 0.0),
            id=raw.get("id"),
            error=raw.get("error"),
            purpose=raw.get("purpose"),
            parts=parts if isinstance(parts, list) else None,
        )

    @staticmethod
    def _provider_blocks(attempts: list[dict[str, Any]] | None) -> list[ProviderAttemptUsage]:
        out: list[ProviderAttemptUsage] = []
        for item in attempts or []:
            outcome = item.get("outcome") or "error"
            if outcome not in {"ok", "empty", "error", "skipped_cooldown"}:
                outcome = "error"
            out.append(
                ProviderAttemptUsage(
                    provider=str(item.get("provider") or "unknown"),
                    outcome=outcome,  # type: ignore[arg-type]
                    latency_ms=float(item.get("latency_ms") or 0.0),
                    error=item.get("error"),
                    cooldown_remaining_seconds=item.get("cooldown_remaining_seconds"),
                    endpoint=item.get("endpoint"),
                    endpoint_attempts=item.get("endpoint_attempts"),
                )
            )
        return out

    def _build_usage(
        self,
        *,
        provider_used: str | None,
        attempts: list[dict[str, Any]] | None,
        openrouter_raw: dict[str, Any] | None,
        cache_hit: bool,
        latency_ms: float,
    ) -> RequestUsage:
        openrouter = self._openrouter_block(openrouter_raw)
        return RequestUsage(
            cache_hit=cache_hit,
            latency_ms=round(latency_ms, 1),
            provider_used=provider_used,
            providers=self._provider_blocks(attempts),
            openrouter=openrouter,
            cost_usd=float(openrouter.cost_usd) if openrouter else 0.0,
        )

    def _log_request(
        self,
        *,
        capability: str,
        query: str,
        provider_used: str | None,
        fallback_chain: list[str],
        latency_ms: float,
        status: str,
        api_key_hint: str = "",
        error: str | None = None,
        openrouter_usage: dict[str, Any] | None = None,
        provider_attempts: list[dict[str, Any]] | None = None,
    ) -> None:
        or_cost = (openrouter_usage or {}).get("cost_usd")
        logger.info(
            "search capability=%s status=%s provider=%s latency_ms=%.1f key=%s chain=%s "
            "openrouter_cost=%s attempts=%s query=%r%s",
            capability,
            status,
            provider_used,
            latency_ms,
            api_key_hint or "-",
            ",".join(fallback_chain),
            or_cost,
            len(provider_attempts or []),
            query[:200],
            f" error={error}" if error else "",
        )

    async def search(self, req: SearchRequest, *, api_key_hint: str = "") -> SearchResponse:
        started = time.perf_counter()
        decision = await self.intent.route(req.query, req.type)
        await self._record_openrouter(decision.openrouter_usage)
        capability: CapabilityName = decision.capability
        if req.scrape and capability == "web_search":
            capability = "search_and_scrape"

        do_structure = want_structured(req.structured, capability)
        cache_params = {
            "limit": req.limit,
            "language": req.language,
            "country": req.country,
            "scrape": req.scrape,
            "formats": req.formats,
            "type": req.type,
            "structured": do_structure,
        }
        cache_key = self.cache.make_key(capability, decision.normalized_query, cache_params)
        cached = await self.cache.get_json(cache_key)
        if cached:
            response = SearchResponse.model_validate(cached)
            latency_ms = (time.perf_counter() - started) * 1000
            # Cache hit: reuse stored provider activity, attach fresh OpenRouter cost for this call.
            prior_attempts = [
                a.model_dump() if hasattr(a, "model_dump") else a
                for a in (response.usage.providers if response.usage else [])
            ] or list(response.meta.get("provider_attempts") or [])
            combined_or = merge_openrouter_usage(
                decision.openrouter_usage,
                response.meta.get("structure_usage") if isinstance(response.meta, dict) else None,
            )
            response.meta = {
                **(response.meta or {}),
                "intent_source": decision.source,
                "confidence": decision.confidence,
                "cache_hit": True,
                "structured": bool(response.structured),
            }
            response.usage = self._build_usage(
                provider_used=response.provider_used,
                attempts=prior_attempts,
                openrouter_raw=combined_or or decision.openrouter_usage,
                cache_hit=True,
                latency_ms=latency_ms,
            )
            await self.usage.record_request(capability=capability, status="ok", cache_hit=True)
            self._log_request(
                capability=capability,
                query=req.query,
                provider_used=response.provider_used,
                fallback_chain=response.fallback_chain,
                latency_ms=latency_ms,
                status="ok",
                api_key_hint=api_key_hint,
                openrouter_usage=combined_or or decision.openrouter_usage,
                provider_attempts=prior_attempts,
            )
            return response

        provider_attempts: list[dict[str, Any]] = []
        structure_usage: dict[str, Any] | None = None
        try:
            if capability == "scrape":
                url = str(decision.entities.get("url") or decision.normalized_query)
                scrape_resp = await self._scrape_url(url, req.formats)
                provider_attempts = scrape_resp["attempts"]
                response = SearchResponse(
                    capability="scrape",
                    provider_used=scrape_resp["provider_used"],
                    fallback_chain=scrape_resp["fallback_chain"],
                    scrape_results=scrape_resp["results"],
                    meta={
                        "intent_source": decision.source,
                        "confidence": decision.confidence,
                        "cache_hit": False,
                    },
                )
            elif capability == "search_and_scrape":
                response = await self._search_and_scrape(req, decision.normalized_query, decision)
                provider_attempts = [
                    a.model_dump() if hasattr(a, "model_dump") else a
                    for a in (response.usage.providers if response.usage else [])
                ]
            else:
                provider_req = ProviderRequest(
                    query=decision.normalized_query,
                    limit=req.limit,
                    language=req.language,
                    country=req.country,
                )
                raw, used, chain, attempts = await self.manager.execute(capability, provider_req)
                provider_attempts = attempts
                response = SearchResponse(
                    capability=capability,
                    provider_used=used,
                    fallback_chain=chain,
                    results=raw.results,
                    meta={
                        "intent_source": decision.source,
                        "confidence": decision.confidence,
                        "cache_hit": False,
                        "errors": raw.raw.get("errors"),
                    },
                )

            if do_structure and response.results and capability != "scrape":
                structured, structure_usage = await self.structure.extract(
                    query=decision.normalized_query or req.query,
                    capability=capability,
                    results=response.results,
                )
                response.structured = structured
                await self._record_openrouter(structure_usage)
                response.meta["structured"] = bool(structured)
                response.meta["structure_usage"] = structure_usage
                if structure_usage and structure_usage.get("error") and not structured:
                    response.meta["structure_error"] = structure_usage.get("error")

            combined_or = merge_openrouter_usage(decision.openrouter_usage, structure_usage)
            latency_ms = (time.perf_counter() - started) * 1000
            response.usage = self._build_usage(
                provider_used=response.provider_used,
                attempts=provider_attempts,
                openrouter_raw=combined_or,
                cache_hit=False,
                latency_ms=latency_ms,
            )
            status = "ok" if (response.results or response.scrape_results) else "empty"
            await self.cache.set_json(cache_key, response.model_dump(mode="json"))
            await self.usage.record_request(capability=capability, status=status, cache_hit=False)
            self._log_request(
                capability=capability,
                query=req.query,
                provider_used=response.provider_used,
                fallback_chain=response.fallback_chain,
                latency_ms=latency_ms,
                status=status,
                api_key_hint=api_key_hint,
                openrouter_usage=combined_or,
                provider_attempts=provider_attempts,
            )
            return response
        except Exception as exc:  # noqa: BLE001
            await self.usage.record_request(capability=capability, status="error", cache_hit=False)
            self._log_request(
                capability=capability,
                query=req.query,
                provider_used=None,
                fallback_chain=[],
                latency_ms=(time.perf_counter() - started) * 1000,
                status="error",
                api_key_hint=api_key_hint,
                error=str(exc),
                openrouter_usage=merge_openrouter_usage(decision.openrouter_usage, structure_usage),
                provider_attempts=provider_attempts,
            )
            raise

    async def _linkedin_cooldown_active(self) -> dict[str, Any] | None:
        """Any Scrapingdog Profile sibling on cooldown blocks the forced endpoint."""
        best: dict[str, Any] | None = None
        for name in COOLDOWN_ALIASES:
            info = await self.cooldown.get(name)
            if not info:
                continue
            if best is None or int(info.get("remaining_seconds") or 0) > int(
                best.get("remaining_seconds") or 0
            ):
                best = info
        return best

    async def _mark_linkedin_cooldown(self, reason: str) -> int | None:
        remaining = None
        for name in COOLDOWN_ALIASES:
            remaining = await self.cooldown.mark(name, reason)
        return remaining

    async def linkedin_profile(
        self,
        req: LinkedInProfileRequest,
        *,
        api_key_hint: str = "",
    ) -> LinkedInProfileResponse:
        """Forced Scrapingdog Profile API — no SERP / Firecrawl / fallback chain."""
        started = time.perf_counter()
        slug, canonical_url = resolve_profile_id(
            kind=req.type,
            profile_id=req.id,
            url=req.url,
        )
        premium = resolve_premium(self.settings, req.premium)

        if not self.settings.scrapingdog_api_key.strip():
            raise ProviderEndpointError(
                "scrapingdog profile unavailable: SCRAPINGDOG_API_KEY not configured",
                endpoint="profile",
                endpoint_attempts=[
                    {
                        "endpoint": "profile",
                        "type": "profile" if req.type == "person" else "company",
                        "skipped": True,
                        "reason": "no_api_key",
                    }
                ],
            )

        cooling = await self._linkedin_cooldown_active()
        if cooling:
            remaining = int(cooling.get("remaining_seconds") or 0)
            latency_ms = (time.perf_counter() - started) * 1000
            attempt = {
                "provider": SCRAPINGDOG_PROFILE,
                "outcome": "skipped_cooldown",
                "latency_ms": 0.0,
                "cooldown_remaining_seconds": remaining,
                "error": f"cooldown:{cooling.get('reason')}",
                "endpoint": "profile",
            }
            await self.usage.record_request(
                capability=f"linkedin_profile_{req.type}",
                status="error",
                cache_hit=False,
            )
            self._log_request(
                capability=f"linkedin_profile_{req.type}",
                query=f"{req.type}:{slug}",
                provider_used=None,
                fallback_chain=[SCRAPINGDOG_PROFILE],
                latency_ms=latency_ms,
                status="error",
                api_key_hint=api_key_hint,
                error=attempt["error"],
                provider_attempts=[attempt],
            )
            raise ProviderEndpointError(
                f"scrapingdog profile cooling down ({remaining}s): {cooling.get('reason')}",
                endpoint="profile",
                endpoint_attempts=[
                    {
                        "endpoint": "profile",
                        "skipped": True,
                        "reason": "cooldown",
                        "cooldown_remaining_seconds": remaining,
                    }
                ],
            )

        provider_attempts: list[dict[str, Any]] = []
        try:
            data, endpoint_attempts = await fetch_scrapingdog_profile(
                settings=self.settings,
                kind=req.type,
                profile_id=slug,
                premium=premium,
            )
            latency_ms = (time.perf_counter() - started) * 1000
            attempt = {
                "provider": SCRAPINGDOG_PROFILE,
                "outcome": "ok",
                "latency_ms": round(latency_ms, 1),
                "endpoint": "profile",
                "endpoint_attempts": endpoint_attempts,
            }
            provider_attempts = [attempt]
            await self.usage.record_provider(
                provider=SCRAPINGDOG_PROFILE,
                outcome="ok",
                latency_ms=latency_ms,
            )
            response = LinkedInProfileResponse(
                type=req.type,
                id=slug,
                url=canonical_url,
                provider_used=SCRAPINGDOG_PROFILE,
                premium=premium,
                profile=unwrap_profile_payload(data),
                usage=self._build_usage(
                    provider_used=SCRAPINGDOG_PROFILE,
                    attempts=provider_attempts,
                    openrouter_raw=None,
                    cache_hit=False,
                    latency_ms=latency_ms,
                ),
                meta={
                    "endpoint": "profile",
                    "scrapingdog_type": "profile" if req.type == "person" else "company",
                },
            )
            await self.usage.record_request(
                capability=f"linkedin_profile_{req.type}",
                status="ok",
                cache_hit=False,
            )
            self._log_request(
                capability=f"linkedin_profile_{req.type}",
                query=f"{req.type}:{slug}",
                provider_used=SCRAPINGDOG_PROFILE,
                fallback_chain=[SCRAPINGDOG_PROFILE],
                latency_ms=latency_ms,
                status="ok",
                api_key_hint=api_key_hint,
                provider_attempts=provider_attempts,
            )
            return response
        except ProviderEndpointError as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            await self.usage.record_provider(
                provider=SCRAPINGDOG_PROFILE,
                outcome="error",
                latency_ms=latency_ms,
            )
            reason = cooldown_reason_from_error(exc)
            cooldown_remaining = None
            if reason:
                cooldown_remaining = await self._mark_linkedin_cooldown(reason)
            attempt = {
                "provider": SCRAPINGDOG_PROFILE,
                "outcome": "error",
                "latency_ms": round(latency_ms, 1),
                "error": str(exc)[:300],
                "cooldown_remaining_seconds": cooldown_remaining or None,
                "endpoint": getattr(exc, "endpoint", None),
                "endpoint_attempts": getattr(exc, "endpoint_attempts", None),
            }
            provider_attempts = [attempt]
            await self.usage.record_request(
                capability=f"linkedin_profile_{req.type}",
                status="error",
                cache_hit=False,
            )
            self._log_request(
                capability=f"linkedin_profile_{req.type}",
                query=f"{req.type}:{slug}",
                provider_used=None,
                fallback_chain=[SCRAPINGDOG_PROFILE],
                latency_ms=latency_ms,
                status="error",
                api_key_hint=api_key_hint,
                error=str(exc),
                provider_attempts=provider_attempts,
            )
            raise
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - started) * 1000
            await self.usage.record_request(
                capability=f"linkedin_profile_{req.type}",
                status="error",
                cache_hit=False,
            )
            self._log_request(
                capability=f"linkedin_profile_{req.type}",
                query=f"{req.type}:{slug}",
                provider_used=None,
                fallback_chain=[SCRAPINGDOG_PROFILE],
                latency_ms=latency_ms,
                status="error",
                api_key_hint=api_key_hint,
                error=str(exc),
                provider_attempts=provider_attempts,
            )
            raise

    async def scrape(self, req: ScrapeRequest, *, api_key_hint: str = "") -> SearchResponse:
        started = time.perf_counter()
        url = str(req.url)
        try:
            scrape_resp = await self._scrape_url(url, req.formats)
            latency_ms = (time.perf_counter() - started) * 1000
            response = SearchResponse(
                capability="scrape",
                provider_used=scrape_resp["provider_used"],
                fallback_chain=scrape_resp["fallback_chain"],
                scrape_results=scrape_resp["results"],
                usage=self._build_usage(
                    provider_used=scrape_resp["provider_used"],
                    attempts=scrape_resp["attempts"],
                    openrouter_raw=None,
                    cache_hit=False,
                    latency_ms=latency_ms,
                ),
                meta={"cache_hit": False},
            )
            status = "ok" if response.scrape_results else "empty"
            await self.usage.record_request(capability="scrape", status=status, cache_hit=False)
            self._log_request(
                capability="scrape",
                query=url,
                provider_used=response.provider_used,
                fallback_chain=response.fallback_chain,
                latency_ms=latency_ms,
                status=status,
                api_key_hint=api_key_hint,
                provider_attempts=scrape_resp["attempts"],
            )
            return response
        except Exception as exc:  # noqa: BLE001
            await self.usage.record_request(capability="scrape", status="error", cache_hit=False)
            self._log_request(
                capability="scrape",
                query=url,
                provider_used=None,
                fallback_chain=[],
                latency_ms=(time.perf_counter() - started) * 1000,
                status="error",
                api_key_hint=api_key_hint,
                error=str(exc),
            )
            raise

    async def _scrape_url(self, url: str, formats: list[str]) -> dict[str, Any]:
        raw, used, chain, attempts = await self.manager.execute(
            "scrape",
            ProviderRequest(url=url, formats=formats),
        )
        return {
            "provider_used": used,
            "fallback_chain": chain,
            "results": raw.scrape_results,
            "attempts": attempts,
        }

    async def _search_and_scrape(
        self,
        req: SearchRequest,
        query: str,
        decision: Any,
    ) -> SearchResponse:
        provider_req = ProviderRequest(
            query=query,
            limit=req.limit,
            language=req.language,
            country=req.country,
        )
        raw, used, chain, attempts = await self.manager.execute("web_search", provider_req)
        scrape_results: list[ScrapeResult] = []
        scrape_chain: list[str] = []
        all_attempts = list(attempts)
        for item in raw.results[: min(3, req.limit)]:
            if not item.url:
                continue
            try:
                part = await self._scrape_url(item.url, req.formats)
                scrape_results.extend(part["results"])
                scrape_chain.extend(part["fallback_chain"])
                all_attempts.extend(part["attempts"])
                if part["results"]:
                    md = part["results"][0].markdown or part["results"][0].text
                    item.content = md
            except Exception as exc:  # noqa: BLE001
                logger.warning("scrape after search failed for %s: %s", item.url, exc)

        return SearchResponse(
            capability="search_and_scrape",
            provider_used=used,
            fallback_chain=chain + scrape_chain,
            results=raw.results,
            scrape_results=scrape_results,
            usage=self._build_usage(
                provider_used=used,
                attempts=all_attempts,
                openrouter_raw=decision.openrouter_usage,
                cache_hit=False,
                latency_ms=0.0,
            ),
            meta={
                "intent_source": decision.source,
                "confidence": decision.confidence,
                "cache_hit": False,
            },
        )
