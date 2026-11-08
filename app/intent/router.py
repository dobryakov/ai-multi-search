from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings, get_settings
from app.normalize.schema import CapabilityName

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s]+", re.I)

PERSON_HINTS = (
    "who is",
    "кто такой",
    "кто такая",
    "find person",
    "найди человека",
    "linkedin",
    "биография",
    "biography",
    "profile of",
)
COMPANY_HINTS = (
    "company",
    "компания",
    "startup",
    "фирм",
    "organization",
    "организация",
    "about company",
    "о компании",
)
SCRAPE_HINTS = ("scrape", "скачай", "прочитай страницу", "extract from url", "parse page")


@dataclass
class IntentDecision:
    capability: CapabilityName
    normalized_query: str
    entities: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    source: str = "explicit"  # explicit | llm | heuristic
    openrouter_usage: dict[str, Any] = field(default_factory=dict)


class IntentRouter:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def route(self, query: str, explicit_type: CapabilityName | None = None) -> IntentDecision:
        q = query.strip()
        if explicit_type:
            return IntentDecision(
                capability=explicit_type,
                normalized_query=q,
                confidence=1.0,
                source="explicit",
            )

        llm = await self._classify_with_llm(q)
        if llm and llm.confidence >= self.settings.intent_confidence_threshold:
            return llm

        # Preserve any openrouter_usage from a low-confidence LLM call on the heuristic result.
        heuristic = self._heuristic(q)
        if llm and llm.openrouter_usage:
            heuristic.openrouter_usage = llm.openrouter_usage
        return heuristic

    async def _classify_with_llm(self, query: str) -> IntentDecision | None:
        """Classify via OpenRouter Jev Decisions API (typed choice, not chat)."""
        api_key = self.settings.resolved_llm_api_key()
        if not api_key:
            return None

        allowed = {
            "web_search",
            "person_search",
            "company_search",
            "scrape",
            "search_and_scrape",
        }
        payload = {
            "model": self.settings.llm_model or "typesafe/jev-1.14",
            "state": query,
            "questions": {
                "capability": {
                    "type": "choice",
                    "instructions": (
                        "Which search capability should handle this user query? "
                        "Pick scrape only when the user primarily wants content from a known URL."
                    ),
                    "criteria": {
                        "web_search": "General web search for facts, how-tos, news, products, or finding URLs",
                        "person_search": "Looking up a specific person, biography, profile, or who-is question",
                        "company_search": "Looking up a company, startup, organization, or business",
                        "scrape": "Fetch/parse content of a specific URL the user already provided",
                        "search_and_scrape": "Search the web and then download page content from results",
                    },
                }
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
                resp = await client.post(
                    self.settings.decisions_url(),
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": self.settings.public_base_url,
                        "X-Title": self.settings.app_name,
                    },
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
            usage_raw = data.get("usage") or {}
            openrouter_usage = {
                "model": data.get("model") or self.settings.llm_model,
                "ok": True,
                "input_tokens": int(usage_raw.get("input_tokens") or 0),
                "output_tokens": int(usage_raw.get("output_tokens") or 0),
                "cost_usd": float(usage_raw.get("cost") or 0.0),
                "id": data.get("id"),
            }
            answer = (data.get("answers") or {}).get("capability") or {}
            capability = answer.get("choice")
            if capability not in allowed:
                return IntentDecision(
                    capability="web_search",
                    normalized_query=query,
                    confidence=0.0,
                    source="llm",
                    openrouter_usage=openrouter_usage,
                )
            confidence = float(answer.get("confidence", 0.0))
            entities: dict[str, Any] = {"probabilities": answer.get("probabilities") or {}}
            urls = URL_RE.findall(query)
            if urls:
                entities["url"] = urls[0]
            return IntentDecision(
                capability=capability,  # type: ignore[arg-type]
                normalized_query=urls[0] if capability == "scrape" and urls else query,
                entities=entities,
                confidence=max(0.0, min(1.0, confidence)),
                source="llm",
                openrouter_usage=openrouter_usage,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Jev intent classification failed: %s", exc)
            return IntentDecision(
                capability="web_search",
                normalized_query=query,
                confidence=0.0,
                source="llm",
                openrouter_usage={
                    "model": self.settings.llm_model,
                    "ok": False,
                    "error": str(exc),
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                },
            )

    def _heuristic(self, query: str) -> IntentDecision:
        lowered = query.lower().strip()
        urls = URL_RE.findall(query)
        if urls and (len(query.strip()) == len(urls[0]) or any(h in lowered for h in SCRAPE_HINTS)):
            return IntentDecision(
                capability="scrape",
                normalized_query=urls[0],
                entities={"url": urls[0]},
                confidence=0.7,
                source="heuristic",
            )
        if any(h in lowered for h in PERSON_HINTS):
            return IntentDecision(
                capability="person_search",
                normalized_query=query,
                confidence=0.6,
                source="heuristic",
            )
        if any(h in lowered for h in COMPANY_HINTS):
            return IntentDecision(
                capability="company_search",
                normalized_query=query,
                confidence=0.6,
                source="heuristic",
            )
        if urls and "scrape" in lowered:
            return IntentDecision(
                capability="search_and_scrape",
                normalized_query=query,
                entities={"url": urls[0]},
                confidence=0.55,
                source="heuristic",
            )
        # Lone URL without keywords still defaults to scrape for convenience.
        parsed = urlparse(query.strip())
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return IntentDecision(
                capability="scrape",
                normalized_query=query.strip(),
                entities={"url": query.strip()},
                confidence=0.65,
                source="heuristic",
            )
        return IntentDecision(
            capability="web_search",
            normalized_query=query,
            confidence=0.5,
            source="heuristic",
        )
