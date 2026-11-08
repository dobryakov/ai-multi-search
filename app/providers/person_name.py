"""Extract a plausible person first/last name from free-text queries."""

from __future__ import annotations

import re

_PREFIX_RE = re.compile(
    r"^(?:"
    r"who\s+is(?:\s+the)?|"
    r"who'?s|"
    r"find(?:\s+(?:me\s+)?(?:person|people|profile))?|"
    r"profile\s+of|"
    r"biography\s+of|"
    r"bio\s+of|"
    r"about|"
    r"look\s+up|"
    r"search\s+for|"
    r"кто\s+так(?:ой|ая)|"
    r"найди(?:\s+человека)?"
    r")\s+",
    re.I,
)

_ROLE_WORDS = {
    "cto",
    "ceo",
    "cfo",
    "coo",
    "cmo",
    "cpo",
    "cio",
    "founder",
    "co-founder",
    "president",
    "director",
    "head",
    "vp",
    "svp",
    "evp",
    "manager",
    "lead",
    "chief",
}

_STOP = _ROLE_WORDS | {
    "who",
    "is",
    "the",
    "a",
    "an",
    "of",
    "at",
    "in",
    "for",
    "and",
    "or",
    "to",
    "from",
    "with",
    "what",
    "where",
    "when",
    "how",
    "find",
    "person",
    "people",
    "profile",
    "about",
    "bio",
    "biography",
    "linkedin",
    "currently",
    "working",
    "works",
    "кто",
    "такой",
    "такая",
    "чем",
    "это",
    "найди",
    "человека",
}


def extract_person_name(query: str) -> tuple[str, str] | None:
    """Return (first_name, last_name) or None when the query is not a person name.

    Examples:
      "Grigoriy Dobryakov" -> ("Grigoriy", "Dobryakov")
      "who is the CTO of Stripe" -> None
      "кто такой Илон Маск" -> ("Илон", "Маск")
    """
    q = (query or "").strip().strip("?.!,;:\"'")
    if not q:
        return None
    q = _PREFIX_RE.sub("", q).strip().strip("?.!,;:\"'")
    parts = [p.strip("?.!,;:\"'") for p in re.split(r"\s+", q) if p.strip("?.!,;:\"'")]
    if not parts:
        return None

    lower = [p.lower() for p in parts]
    # Role + organization ("CTO of Stripe") is not a personal name.
    if any(p in _ROLE_WORDS for p in lower) and "of" in lower:
        return None

    while parts and parts[0].lower() in _STOP:
        parts.pop(0)
    while parts and parts[-1].lower() in _STOP:
        parts.pop()
    if not parts:
        return None
    if any(p.lower() in _ROLE_WORDS for p in parts):
        return None

    if len(parts) == 1:
        token = parts[0]
        if token[0].isalpha() and token.lower() not in _STOP:
            return token, token
        return None

    first, last = parts[0], " ".join(parts[1:])
    if not first[0].isalpha() or not last[0].isalpha():
        return None
    return first, last
