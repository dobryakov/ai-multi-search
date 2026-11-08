from __future__ import annotations

from app.providers.company_slug import linkedin_company_slug


def test_slug_from_url():
    assert linkedin_company_slug("https://www.linkedin.com/company/amazon") == "amazon"
    assert linkedin_company_slug("linkedin.com/company/askona-group/") == "askona-group"


def test_slug_from_plain_token():
    assert linkedin_company_slug("Amazon") == "amazon"
    assert linkedin_company_slug("askona") == "askona"


def test_slug_from_mixed_corp_prefix():
    # "ГК" stripped as corp token → latin remainder becomes slug
    assert linkedin_company_slug("ГК MILLHOUSE") == "millhouse"


def test_slug_skips_non_latin_names():
    assert linkedin_company_slug("Аскона") is None
    assert linkedin_company_slug("группа компаний мебель оптом") is None
