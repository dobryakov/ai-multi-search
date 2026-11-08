from __future__ import annotations

from app.capabilities.registry import CapabilityRegistry
from app.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from app.providers.person_name import extract_person_name


def test_extract_plain_name():
    assert extract_person_name("Grigoriy Dobryakov") == ("Grigoriy", "Dobryakov")
    assert extract_person_name("кто такой Илон Маск") == ("Илон", "Маск")


def test_extract_rejects_role_queries():
    assert extract_person_name("who is the CTO of Stripe") is None
    assert extract_person_name("who is CTO of Stripe") is None
    assert extract_person_name("CEO of OpenAI") is None


def test_extract_strips_who_is_prefix():
    assert extract_person_name("who is Grigoriy Dobryakov") == ("Grigoriy", "Dobryakov")


def test_extract_rejects_single_token_and_urls():
    # Vanity ids must not become ("x","x") → "x x" and break Profile-by-id.
    assert extract_person_name("rbranson") is None
    assert extract_person_name("dobryakov") is None
    assert extract_person_name("https://www.linkedin.com/in/rbranson") is None
    assert extract_person_name("linkedin.com/in/dobryakov") is None


class _Web(BaseProvider):
    name = "w"
    capability = "web_search"
    priority = 1
    cost = 1

    def available(self) -> bool:
        return True

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse()


class _Scrape(BaseProvider):
    name = "s"
    capability = "scrape"
    priority = 1
    cost = 1

    def available(self) -> bool:
        return True

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse()


def test_search_and_scrape_available_when_web_and_scrape_exist():
    desc = CapabilityRegistry([_Web(), _Scrape()]).describe()
    sas = next(c for c in desc.capabilities if c.name == "search_and_scrape")
    assert sas.available is True
    assert sas.providers[0].name == "composite:web_search+scrape"


def test_search_and_scrape_unavailable_without_scrape():
    desc = CapabilityRegistry([_Web()]).describe()
    sas = next(c for c in desc.capabilities if c.name == "search_and_scrape")
    assert sas.available is False
