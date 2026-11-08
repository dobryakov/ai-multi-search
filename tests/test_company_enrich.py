from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.enrich.company_facets import (
    ENRICH_PURPOSE,
    FACET_TEMPLATES,
    build_facet_queries,
    build_primary_company_query,
    company_name_for_facets,
    merge_primary_and_facet_hits,
)
from app.normalize.schema import SearchRequest, SearchResultItem
from app.providers.base import ProviderResponse
from app.services.search import SearchService
from app.structure.extractor import StructureExtractor, _schema_for


def test_company_name_for_facets_strips_prefix():
    assert company_name_for_facets('about "Stripe"') == "Stripe"
    assert company_name_for_facets("компания Яндекс") == "Яндекс"
    assert company_name_for_facets("Acme Corp") == "Acme Corp"


def test_build_facet_queries_engage_set():
    queries = build_facet_queries("Stripe")
    assert len(queries) == 5
    ids = [q[0] for q in queries]
    assert ids == ["culture", "hiring", "funding", "vendors", "initiatives"]
    assert all("Stripe" in q for _, q in queries)
    assert len(FACET_TEMPLATES) == 5


def test_funding_primary_and_facet_allowlist_with_context():
    primary = build_primary_company_query("Pigment", "Software Development")
    assert primary == (
        '"Pigment" software company news OR funding OR acquisition'
    )
    queries = build_facet_queries(
        "Pigment",
        allowlist=["funding", "vendors", "initiatives"],
        context="Software Development",
    )
    assert [facet for facet, _ in queries] == ["funding", "vendors", "initiatives"]
    assert all('"Pigment" software' in query for _, query in queries)


def test_merge_primary_and_facet_hits_dedupes_and_tags():
    primary = [
        SearchResultItem(title="A", url="https://a.example/", snippet="", source="serper"),
        SearchResultItem(title="B", url="https://b.example/", snippet="", source="serper"),
    ]
    facets = [
        (
            "culture",
            [
                SearchResultItem(
                    title="dup",
                    url="https://a.example/",
                    snippet="x",
                    source="brave",
                ),
                SearchResultItem(
                    title="culture hit",
                    url="https://c.example/",
                    snippet="y",
                    source="brave",
                ),
            ],
        ),
        (
            "funding",
            [
                SearchResultItem(
                    title="fund",
                    url="https://d.example/",
                    snippet="z",
                    source="serper",
                ),
            ],
        ),
    ]
    merged = merge_primary_and_facet_hits(primary, facets)
    urls = [m.url for m in merged]
    assert urls == [
        "https://a.example/",
        "https://b.example/",
        "https://c.example/",
        "https://d.example/",
    ]
    assert merged[0].extra.get("facet") is None
    assert merged[2].extra.get("facet") == "culture"
    assert merged[3].extra.get("facet") == "funding"


def test_enrich_schema_fields():
    base = _schema_for("company_search", enrich=False)
    rich = _schema_for("company_search", enrich=True)
    assert "culture" not in base
    assert "culture" in rich
    assert "vendor_engagement" in rich
    assert "funding" in rich
    assert isinstance(rich["initiatives"], list)


def test_search_request_enrich_default_false():
    req = SearchRequest(query="Stripe")
    assert req.enrich is False


@pytest.mark.asyncio
async def test_enrich_false_skips_facets(monkeypatch):
    settings = Settings(openrouter_api_key="", structure_model="")
    service = SearchService(settings=settings)
    service.intent = AsyncMock()
    service.intent.route = AsyncMock(
        return_value=type(
            "D",
            (),
            {
                "capability": "company_search",
                "normalized_query": "Stripe",
                "entities": {},
                "confidence": 1.0,
                "source": "explicit",
                "openrouter_usage": {},
            },
        )()
    )
    service.cache.get_json = AsyncMock(return_value=None)
    service.cache.set_json = AsyncMock()
    service.usage.record_request = AsyncMock()
    service.usage.record_openrouter = AsyncMock()

    primary = ProviderResponse(
        results=[
            SearchResultItem(
                title="Stripe",
                url="https://stripe.com",
                snippet="payments",
                source="serper",
            )
        ]
    )
    service.manager.execute = AsyncMock(return_value=(primary, "serper", ["serper"], []))
    enrich_spy = AsyncMock()
    service._enrich_company_facets = enrich_spy  # type: ignore[method-assign]

    resp = await service.search(
        SearchRequest(query="Stripe", type="company_search", enrich=False, structured=False),
        api_key_hint="test",
    )
    enrich_spy.assert_not_called()
    assert resp.meta.get("enrich") is None
    assert len(resp.results) == 1


@pytest.mark.asyncio
async def test_enrich_true_runs_sequential_facets(monkeypatch):
    settings = Settings(
        openrouter_api_key="",
        structure_model="",
        enrich_facet_limit=2,
        enrich_facet_delay_ms=0,
    )
    service = SearchService(settings=settings)
    service.intent = AsyncMock()
    service.intent.route = AsyncMock(
        return_value=type(
            "D",
            (),
            {
                "capability": "company_search",
                "normalized_query": "Stripe",
                "entities": {},
                "confidence": 1.0,
                "source": "explicit",
                "openrouter_usage": {},
            },
        )()
    )
    service.cache.get_json = AsyncMock(return_value=None)
    service.cache.set_json = AsyncMock()
    service.usage.record_request = AsyncMock()
    service.usage.record_openrouter = AsyncMock()

    call_n = {"n": 0}

    async def fake_execute(capability, request):  # noqa: ANN001
        call_n["n"] += 1
        if call_n["n"] == 1:
            assert capability == "web_search"
            assert request.query == '"Stripe" company news OR funding OR acquisition'
            return (
                ProviderResponse(
                    results=[
                        SearchResultItem(
                            title="Stripe",
                            url="https://stripe.com",
                            snippet="payments",
                            source="serper",
                        )
                    ]
                ),
                "serper_company",
                ["serper_company"],
                [{"provider": "serper_company", "outcome": "ok", "latency_ms": 1.0}],
            )
        assert capability == "web_search"
        facet_url = f"https://facet.example/{call_n['n']}"
        return (
            ProviderResponse(
                results=[
                    SearchResultItem(
                        title=f"hit-{call_n['n']}",
                        url=facet_url,
                        snippet=request.query[:80],
                        source="serper",
                    )
                ]
            ),
            "serper",
            ["serper"],
            [{"provider": "serper", "outcome": "ok", "latency_ms": 1.0}],
        )

    service.manager.execute = AsyncMock(side_effect=fake_execute)

    resp = await service.search(
        SearchRequest(query="Stripe", type="company_search", enrich=True, structured=False, limit=3),
        api_key_hint="test",
    )
    assert resp.meta.get("enrich") is True
    assert resp.meta.get("enrich_purpose") == ENRICH_PURPOSE
    assert resp.meta.get("enrich_facet_limit") == 2
    assert len(resp.meta.get("facets") or []) == 5
    # primary + 5 facet hits
    assert len(resp.results) == 6
    assert resp.results[0].url == "https://stripe.com"
    assert all(r.extra.get("facet") for r in resp.results)
    assert resp.results[0].extra.get("facet") == "funding"
    assert resp.meta["enrich_primary_query"] == (
        '"Stripe" company news OR funding OR acquisition'
    )
    # funding/news primary once + 5 web_search facets
    assert service.manager.execute.await_count == 6


@pytest.mark.asyncio
async def test_enrich_allowlist_limits_facets(monkeypatch):
    settings = Settings(openrouter_api_key="", structure_model="", enrich_facet_delay_ms=0)
    service = SearchService(settings=settings)
    service.intent = AsyncMock()
    service.intent.route = AsyncMock(
        return_value=type(
            "D",
            (),
            {
                "capability": "company_search",
                "normalized_query": "Pigment",
                "entities": {},
                "confidence": 1.0,
                "source": "explicit",
                "openrouter_usage": {},
            },
        )()
    )
    service.cache.get_json = AsyncMock(return_value=None)
    service.cache.set_json = AsyncMock()
    service.usage.record_request = AsyncMock()
    service.usage.record_openrouter = AsyncMock()
    service.manager.execute = AsyncMock(
        return_value=(ProviderResponse(results=[]), "searxng", ["searxng"], [])
    )

    resp = await service.search(
        SearchRequest(
            query="Pigment",
            type="company_search",
            enrich=True,
            enrich_facets=["funding", "vendors", "initiatives"],
            enrich_context="Software Development",
            structured=False,
        )
    )
    assert [f["id"] for f in resp.meta["facets"]] == [
        "funding",
        "vendors",
        "initiatives",
    ]
    assert service.manager.execute.await_count == 4
    first_request = service.manager.execute.await_args_list[0].args[1]
    assert first_request.query == (
        '"Pigment" software company news OR funding OR acquisition'
    )


@pytest.mark.asyncio
async def test_structure_enrich_uses_extended_schema(monkeypatch):
    settings = Settings(
        openrouter_api_key="test-key",
        structure_model="google/gemini-2.5-flash-lite",
        llm_base_url="https://openrouter.ai/api",
    )
    extractor = StructureExtractor(settings)
    captured: dict = {}

    async def fake_post(self, url, headers=None, json=None):  # noqa: ANN001
        captured["json"] = json
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "id": "gen-enrich",
                "model": "google/gemini-2.5-flash-lite",
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"kind":"company","name":"Stripe","legal_name":"",'
                                '"domain":"stripe.com","industry":"","headquarters":"",'
                                '"linkedin_url":"","website":"https://stripe.com",'
                                '"description":"","confidence":0.8,"evidence_urls":[],'
                                '"culture":"engineering-led","hiring":"multi-stage",'
                                '"funding":[],"vendor_engagement":"",'
                                '"initiatives":[],"pros":["brand"],"cons":[],'
                                '"career_pages":[],"partner_pages":[],'
                                '"enrichment_summary":"Strong brand for contractors."}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "cost": 0.0},
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    structured, usage = await extractor.extract(
        query="Stripe",
        capability="company_search",
        enrich=True,
        results=[
            SearchResultItem(
                title="Stripe",
                url="https://stripe.com",
                snippet="payments",
                source="serper",
            ),
            SearchResultItem(
                title="Culture",
                url="https://example.com/culture",
                snippet="values",
                source="serper",
                extra={"facet": "culture"},
            ),
        ],
    )
    assert structured is not None
    assert structured["culture"] == "engineering-led"
    assert usage["ok"] is True
    user_payload = captured["json"]["messages"][1]["content"]
    assert '"enrich": true' in user_payload or '"enrich": true'.replace(" ", "") in user_payload.replace(
        " ", ""
    )
    assert "vendor_engagement" in user_payload
