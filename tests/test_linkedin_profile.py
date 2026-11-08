from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.normalize.schema import LinkedInProfileRequest
from app.providers.cooldown import ProviderCooldownout, set_provider_cooldown
from app.providers.errors import ProviderEndpointError
from app.providers.scrapingdog_profile import (
    LinkedInProfileValidationError,
    resolve_profile_id,
)
from app.services.search import SearchService
from app.usage.tracker import UsageTracker, set_usage_tracker


def test_resolve_person_id_and_url():
    slug, url = resolve_profile_id(kind="person", profile_id="Dobryakov", url=None)
    assert slug == "dobryakov"
    assert url == "https://www.linkedin.com/in/dobryakov"

    slug, url = resolve_profile_id(
        kind="person",
        profile_id=None,
        url="https://www.linkedin.com/in/rbranson/",
    )
    assert slug == "rbranson"
    assert url == "https://www.linkedin.com/in/rbranson"


def test_resolve_company_id_and_url():
    slug, url = resolve_profile_id(kind="company", profile_id="JetBrains", url=None)
    assert slug == "jetbrains"
    assert url == "https://www.linkedin.com/company/jetbrains"

    slug, url = resolve_profile_id(
        kind="company",
        profile_id=None,
        url="https://www.linkedin.com/company/amazon",
    )
    assert slug == "amazon"


def test_resolve_rejects_free_text_and_xor():
    with pytest.raises(LinkedInProfileValidationError):
        resolve_profile_id(kind="company", profile_id="JetBrains CEO", url=None)
    with pytest.raises(LinkedInProfileValidationError):
        resolve_profile_id(kind="person", profile_id="Grigoriy Dobryakov", url=None)
    with pytest.raises(LinkedInProfileValidationError):
        resolve_profile_id(kind="person", profile_id=None, url=None)
    with pytest.raises(LinkedInProfileValidationError):
        resolve_profile_id(kind="person", profile_id="a", url="https://www.linkedin.com/in/a")
    with pytest.raises(LinkedInProfileValidationError):
        resolve_profile_id(
            kind="person",
            profile_id=None,
            url="https://www.linkedin.com/company/amazon",
        )


def _mock_http(payload: dict, status_code: int = 200):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = payload
    mock_resp.text = "" if status_code < 400 else "upstream error"
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client, mock_resp


@pytest.mark.asyncio
async def test_service_person_profile_lossless():
    settings = Settings(scrapingdog_api_key="test-key", scrapingdog_linkedin_premium=True)
    usage = UsageTracker(settings=settings)
    set_usage_tracker(usage)
    cooldown = ProviderCooldownout(settings=settings)
    set_provider_cooldown(cooldown)
    service = SearchService(settings=settings, usage=usage)
    service.cooldown = cooldown

    payload = {
        "fullName": "Grigoriy Dobryakov",
        "headline": "Head of AI",
        "location": "EU",
        "public_identifier": "dobryakov",
        "experience": [{"title": "Engineer"}],
        "activities": [{"text": "posted"}],
        "about": "bio",
    }
    mock_client, _ = _mock_http(payload)

    with patch("app.providers.scrapingdog_profile.get_http_client", return_value=mock_client):
        out = await service.linkedin_profile(
            LinkedInProfileRequest(type="person", id="dobryakov")
        )

    assert out.provider_used == "scrapingdog_profile"
    assert out.id == "dobryakov"
    assert out.premium is True
    assert out.profile["experience"] == [{"title": "Engineer"}]
    assert out.profile["activities"] == [{"text": "posted"}]
    assert len(out.usage.providers) == 1
    assert out.usage.providers[0].provider == "scrapingdog_profile"
    assert out.usage.providers[0].outcome == "ok"
    params = mock_client.get.await_args.kwargs["params"]
    assert params["type"] == "profile"
    assert params["id"] == "dobryakov"
    assert params["premium"] == "true"


@pytest.mark.asyncio
async def test_service_company_profile_not_serp():
    settings = Settings(scrapingdog_api_key="test-key")
    usage = UsageTracker(settings=settings)
    service = SearchService(settings=settings, usage=usage)
    service.cooldown = ProviderCooldownout(settings=settings)

    payload = {
        "company_name": "JetBrains",
        "headquarters": "Prague",
        "employees": [{"name": "A"}],
        "universal_name_id": "jetbrains",
    }
    mock_client, _ = _mock_http(payload)

    with patch("app.providers.scrapingdog_profile.get_http_client", return_value=mock_client):
        out = await service.linkedin_profile(
            LinkedInProfileRequest(type="company", id="jetbrains")
        )

    assert out.type == "company"
    assert out.profile["company_name"] == "JetBrains"
    assert out.profile["employees"] == [{"name": "A"}]
    assert out.usage.provider_used == "scrapingdog_profile"
    assert all(p.provider == "scrapingdog_profile" for p in out.usage.providers)
    params = mock_client.get.await_args.kwargs["params"]
    assert params["type"] == "company"
    assert "q" not in params


@pytest.mark.asyncio
async def test_service_propagates_upstream_status():
    settings = Settings(scrapingdog_api_key="test-key")
    service = SearchService(settings=settings, usage=UsageTracker(settings=settings))
    service.cooldown = ProviderCooldownout(settings=settings)
    mock_client, _ = _mock_http({}, status_code=400)

    with patch("app.providers.scrapingdog_profile.get_http_client", return_value=mock_client):
        with pytest.raises(ProviderEndpointError) as ei:
            await service.linkedin_profile(LinkedInProfileRequest(type="person", id="x"))
    assert ei.value.endpoint_attempts[0]["status_code"] == 400


def test_http_endpoint_acceptance(monkeypatch):
    monkeypatch.setenv("API_KEYS", "test-key")
    monkeypatch.setenv("SCRAPINGDOG_API_KEY", "sd-key")
    get_settings.cache_clear()

    from app.main import create_app

    app = create_app()
    person_payload = {
        "experience": [{"title": "X"}],
        "activities": [{"text": "Y"}],
        "fullName": "G",
    }
    company_payload = {"company_name": "JetBrains", "employees": []}

    async def fake_get(url, params=None):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        if params and params.get("type") == "company":
            mock_resp.json.return_value = company_payload
        else:
            mock_resp.json.return_value = person_payload
        mock_resp.text = ""
        return mock_resp

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    headers = {"Authorization": "Bearer test-key"}
    with patch("app.providers.scrapingdog_profile.get_http_client", return_value=mock_client):
        with TestClient(app) as client:
            bad = client.post(
                "/v1/linkedin/profile",
                headers=headers,
                json={"type": "company", "id": "JetBrains CEO"},
            )
            assert bad.status_code == 400

            person = client.post(
                "/v1/linkedin/profile",
                headers=headers,
                json={"type": "person", "id": "dobryakov"},
            )
            assert person.status_code == 200
            body = person.json()
            assert body["profile"]["experience"]
            assert body["profile"]["activities"]
            assert body["provider_used"] == "scrapingdog_profile"
            assert len(body["usage"]["providers"]) == 1
            assert body["usage"]["providers"][0]["provider"] == "scrapingdog_profile"

            company = client.post(
                "/v1/linkedin/profile",
                headers=headers,
                json={"type": "company", "id": "jetbrains"},
            )
            assert company.status_code == 200
            cbody = company.json()
            assert cbody["profile"]["company_name"] == "JetBrains"
            assert cbody["usage"]["providers"][0]["provider"] == "scrapingdog_profile"

    get_settings.cache_clear()
