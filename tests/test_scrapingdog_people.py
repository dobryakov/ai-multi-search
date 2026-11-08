from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.providers.base import ProviderRequest
from app.providers.person_scrapingdog import ScrapingdogPeopleProvider


@pytest.mark.asyncio
async def test_scrapingdog_people_profile_by_id():
    settings = Settings(scrapingdog_api_key="test-key")
    provider = ScrapingdogPeopleProvider(settings)
    payload = {
        "fullName": "Richard Branson",
        "headline": "Founder",
        "about": "Entrepreneur",
        "linkedin_url": "https://www.linkedin.com/in/rbranson",
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload
    mock_resp.text = ""

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.providers.person_scrapingdog.get_http_client", return_value=mock_client
    ):
        out = await provider.run(
            ProviderRequest(
                query="https://www.linkedin.com/in/rbranson",
                limit=1,
                extra={"linkedin_person_slug": "rbranson"},
            )
        )

    assert len(out.results) == 1
    assert out.results[0].title == "Richard Branson"
    assert out.results[0].url == "https://www.linkedin.com/in/rbranson"
    assert out.results[0].source == "scrapingdog_people"
    assert out.raw["endpoint"] == "profile"
    params = mock_client.get.await_args.kwargs["params"]
    assert params["type"] == "profile"
    assert params["id"] == "rbranson"


@pytest.mark.asyncio
async def test_scrapingdog_people_skips_free_text_name():
    settings = Settings(scrapingdog_api_key="test-key")
    provider = ScrapingdogPeopleProvider(settings)
    out = await provider.run(ProviderRequest(query="Grigoriy Dobryakov", limit=3))
    assert out.results == []
    assert out.raw["endpoint_attempts"][0]["skipped"] is True
    assert out.raw["endpoint_attempts"][0]["reason"] == "no_person_slug"
