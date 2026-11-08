from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.normalize.schema import SearchResultItem
from app.structure.extractor import (
    StructureExtractor,
    merge_openrouter_usage,
    want_structured,
)


def test_want_structured_auto():
    assert want_structured(None, "person_search") is True
    assert want_structured(None, "company_search") is True
    assert want_structured(None, "web_search") is False
    assert want_structured(True, "web_search") is True
    assert want_structured(False, "person_search") is False


def test_merge_openrouter_usage():
    merged = merge_openrouter_usage(
        {"model": "jev", "ok": True, "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001},
        {
            "model": "flash",
            "ok": True,
            "input_tokens": 20,
            "output_tokens": 8,
            "cost_usd": 0.002,
            "purpose": "structure",
        },
    )
    assert merged is not None
    assert merged["input_tokens"] == 30
    assert merged["output_tokens"] == 13
    assert merged["cost_usd"] == pytest.approx(0.003)
    assert len(merged["parts"]) == 2


@pytest.mark.asyncio
async def test_structure_extractor_person(monkeypatch):
    settings = Settings(
        openrouter_api_key="test-key",
        structure_model="google/gemini-2.5-flash-lite",
        llm_base_url="https://openrouter.ai/api",
    )
    extractor = StructureExtractor(settings)

    async def fake_post(self, url, headers=None, json=None):  # noqa: ANN001
        assert "chat/completions" in url
        assert json["model"] == "google/gemini-2.5-flash-lite"
        assert json["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "id": "gen-test",
                "model": "google/gemini-2.5-flash-lite",
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"kind":"person","name":"Ada Lovelace","headline":"Mathematician",'
                                '"organization":"","role":"","location":"","linkedin_url":"",'
                                '"website":"","summary":"Pioneer","confidence":0.9,'
                                '"evidence_urls":["https://example.com"]}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40, "cost": 0.00001},
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    structured, usage = await extractor.extract(
        query="Ada Lovelace",
        capability="person_search",
        results=[
            SearchResultItem(
                title="Ada Lovelace",
                url="https://example.com",
                snippet="Mathematician",
                source="serper",
            )
        ],
    )
    assert structured is not None
    assert structured["name"] == "Ada Lovelace"
    assert usage["ok"] is True
    assert usage["purpose"] == "structure"
    assert usage["cost_usd"] == pytest.approx(0.00001)
