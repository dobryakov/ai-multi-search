"""Cheap OpenRouter chat model → structured entity JSON from search hits."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.normalize.schema import CapabilityName, SearchResultItem

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{[\s\S]*\}")

_PERSON_SCHEMA = {
    "kind": "person",
    "name": "",
    "headline": "",
    "organization": "",
    "role": "",
    "location": "",
    "linkedin_url": "",
    "website": "",
    "summary": "",
    "confidence": 0.0,
    "evidence_urls": [],
}

_COMPANY_SCHEMA = {
    "kind": "company",
    "name": "",
    "legal_name": "",
    "domain": "",
    "industry": "",
    "headquarters": "",
    "linkedin_url": "",
    "website": "",
    "description": "",
    "confidence": 0.0,
    "evidence_urls": [],
}

_WEB_SCHEMA = {
    "kind": "web",
    "answer_summary": "",
    "entities": [],
    "confidence": 0.0,
    "evidence_urls": [],
}


def want_structured(structured: bool | None, capability: CapabilityName) -> bool:
    """None = auto-on for person/company; explicit bool overrides."""
    if structured is False:
        return False
    if structured is True:
        return True
    return capability in {"person_search", "company_search"}


def _schema_for(capability: CapabilityName) -> dict[str, Any]:
    if capability == "person_search":
        return dict(_PERSON_SCHEMA)
    if capability == "company_search":
        return dict(_COMPANY_SCHEMA)
    return dict(_WEB_SCHEMA)


def _chat_url(settings: Settings) -> str:
    base = (settings.llm_base_url or "https://openrouter.ai/api").rstrip("/")
    if base.endswith("/v1"):
        return base + "/chat/completions"
    if base.endswith("/api"):
        return base + "/v1/chat/completions"
    return base + "/v1/chat/completions"


def _parse_json_content(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        raise ValueError("empty model content")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_RE.search(text)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("structured payload is not an object")
    return data


class StructureExtractor:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def configured(self) -> bool:
        return bool(self.settings.resolved_llm_api_key() and self.settings.structure_model.strip())

    async def extract(
        self,
        *,
        query: str,
        capability: CapabilityName,
        results: list[SearchResultItem],
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Return (structured_payload_or_None, openrouter_usage)."""
        usage: dict[str, Any] = {
            "model": self.settings.structure_model,
            "ok": False,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "id": None,
            "error": None,
            "purpose": "structure",
        }
        if not self.configured():
            usage["error"] = "structure model / OpenRouter key not configured"
            return None, usage
        if not results:
            usage["ok"] = True
            usage["error"] = "no results to structure"
            return None, usage

        schema = _schema_for(capability)
        hits = []
        for item in results[:8]:
            hits.append(
                {
                    "title": item.title,
                    "url": item.url,
                    "snippet": (item.snippet or "")[:400],
                    "source": item.source,
                }
            )
        system = (
            "You extract structured facts from search results. "
            "Return ONLY a JSON object matching the provided template. "
            "Use empty strings / empty arrays when unknown. "
            "confidence is 0..1. Prefer LinkedIn/official sites as evidence_urls. "
            "For linkedin_url use a profile or company page "
            "(linkedin.com/in/... or linkedin.com/company/...), never a /posts/ URL. "
            "For company domain prefer the primary brand website "
            "(not a country microsite or random hit) when several are present. "
            "Do not invent facts that are not supported by the hits."
        )
        user = {
            "query": query,
            "capability": capability,
            "template": schema,
            "hits": hits,
        }
        payload = {
            "model": self.settings.structure_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": 700,
        }
        api_key = self.settings.resolved_llm_api_key()
        try:
            async with httpx.AsyncClient(timeout=self.settings.structure_timeout_seconds) as client:
                resp = await client.post(
                    _chat_url(self.settings),
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
            usage.update(
                {
                    "model": data.get("model") or self.settings.structure_model,
                    "ok": True,
                    "input_tokens": int(
                        usage_raw.get("prompt_tokens")
                        or usage_raw.get("input_tokens")
                        or 0
                    ),
                    "output_tokens": int(
                        usage_raw.get("completion_tokens")
                        or usage_raw.get("output_tokens")
                        or 0
                    ),
                    "cost_usd": float(usage_raw.get("cost") or 0.0),
                    "id": data.get("id"),
                    "error": None,
                }
            )
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            structured = _parse_json_content(content)
            if "kind" not in structured:
                structured["kind"] = schema["kind"]
            return structured, usage
        except Exception as exc:  # noqa: BLE001
            logger.warning("Structure extraction failed: %s", exc)
            usage["error"] = str(exc)[:300]
            return None, usage


def merge_openrouter_usage(
    intent_usage: dict[str, Any] | None,
    structure_usage: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Combine intent + structure OpenRouter usage into one block for the response."""
    tagged: list[dict[str, Any]] = []
    if intent_usage:
        item = dict(intent_usage)
        item.setdefault("purpose", "intent")
        tagged.append(item)
    if structure_usage:
        item = dict(structure_usage)
        item.setdefault("purpose", "structure")
        tagged.append(item)
    if not tagged:
        return None
    if len(tagged) == 1:
        return tagged[0]
    return {
        "model": "+".join(str(p.get("model") or "?") for p in tagged if p.get("model")) or None,
        "ok": all(bool(p.get("ok", True)) for p in tagged),
        "input_tokens": sum(int(p.get("input_tokens") or 0) for p in tagged),
        "output_tokens": sum(int(p.get("output_tokens") or 0) for p in tagged),
        "cost_usd": sum(float(p.get("cost_usd") or 0.0) for p in tagged),
        "id": tagged[-1].get("id") or tagged[0].get("id"),
        "error": next((p.get("error") for p in tagged if p.get("error")), None),
        "parts": [
            {
                "purpose": p.get("purpose"),
                "model": p.get("model"),
                "ok": p.get("ok"),
                "input_tokens": p.get("input_tokens"),
                "output_tokens": p.get("output_tokens"),
                "cost_usd": p.get("cost_usd"),
                "id": p.get("id"),
                "error": p.get("error"),
            }
            for p in tagged
        ],
    }
