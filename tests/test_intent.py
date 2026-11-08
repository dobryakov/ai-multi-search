import pytest

from app.intent.router import IntentRouter


@pytest.mark.asyncio
async def test_explicit_type_skips_llm():
    router = IntentRouter()
    decision = await router.route("anything", explicit_type="person_search")
    assert decision.capability == "person_search"
    assert decision.source == "explicit"


@pytest.mark.asyncio
async def test_heuristic_person(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    from app.config import get_settings

    get_settings.cache_clear()
    router = IntentRouter(get_settings())
    decision = await router.route("Who is Ada Lovelace")
    assert decision.capability == "person_search"
    assert decision.source == "heuristic"


@pytest.mark.asyncio
async def test_heuristic_url_scrape():
    router = IntentRouter()
    decision = await router.route("https://example.com/page")
    assert decision.capability == "scrape"


@pytest.mark.asyncio
async def test_heuristic_default_web():
    router = IntentRouter()
    decision = await router.route("best open source vector databases")
    assert decision.capability == "web_search"
