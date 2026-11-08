from __future__ import annotations

import pytest

from app.config import Settings
from app.intent.router import IntentRouter


@pytest.mark.asyncio
async def test_jev_classify_uses_decisions_api(httpx_mock=None):
    # Lightweight unit test with monkeypatched httpx response via custom transport.
    import httpx

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/alpha/decisions")
            body = {
                "answers": {
                    "capability": {
                        "type": "choice",
                        "choice": "company_search",
                        "probabilities": {"company_search": 0.9, "web_search": 0.1},
                        "confidence": 0.91,
                    }
                }
            }
            return httpx.Response(200, json=body)

    settings = Settings(
        openrouter_api_key="test-or-key",
        llm_api_key="",
        llm_base_url="https://openrouter.ai/api",
        llm_model="typesafe/jev-1.14",
        intent_confidence_threshold=0.55,
    )
    router = IntentRouter(settings)

    original = httpx.AsyncClient

    class PatchedClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = Transport()
            super().__init__(*args, **kwargs)

    import app.intent.router as router_mod

    router_mod.httpx.AsyncClient = PatchedClient  # type: ignore[misc]
    try:
        decision = await router.route("Tell me about Anthropic the AI lab")
    finally:
        router_mod.httpx.AsyncClient = original  # type: ignore[misc]

    assert decision.source == "llm"
    assert decision.capability == "company_search"
    assert decision.confidence >= 0.9
