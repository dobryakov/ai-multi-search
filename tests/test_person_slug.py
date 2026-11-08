from __future__ import annotations

from app.providers.person_slug import linkedin_people_query, linkedin_person_slug


def test_slug_from_url():
    assert linkedin_person_slug("https://www.linkedin.com/in/rbranson") == "rbranson"
    assert linkedin_person_slug("linkedin.com/in/dobryakov/") == "dobryakov"
    assert linkedin_person_slug("https://il.linkedin.com/in/sergey-lokshin") == "sergey-lokshin"


def test_slug_from_bare_id():
    assert linkedin_person_slug("rbranson") == "rbranson"
    assert linkedin_person_slug("Ada-Lovelace") == "ada-lovelace"


def test_slug_rejects_free_text_name():
    assert linkedin_person_slug("Grigoriy Dobryakov") is None
    assert linkedin_person_slug("who is Ada Lovelace") is None


def test_slug_strips_site_suffix():
    assert linkedin_person_slug("rbranson site:linkedin.com/in") == "rbranson"
    assert (
        linkedin_person_slug("https://linkedin.com/in/dobryakov site:linkedin.com/in")
        == "dobryakov"
    )


def test_people_query_appends_site():
    assert linkedin_people_query("Grigoriy Dobryakov") == (
        "Grigoriy Dobryakov site:linkedin.com/in"
    )
    assert linkedin_people_query("Ada Lovelace site:linkedin.com/in") == (
        "Ada Lovelace site:linkedin.com/in"
    )
