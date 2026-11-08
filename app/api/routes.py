from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.api_keys import require_api_key
from app.cache.redis import get_cache
from app.normalize.schema import (
    CapabilitiesResponse,
    LinkedInProfileRequest,
    LinkedInProfileResponse,
    ScrapeRequest,
    SearchRequest,
    SearchResponse,
)
from app.providers.errors import ProviderEndpointError
from app.providers.scrapingdog_profile import LinkedInProfileValidationError
from app.services.search import SearchService

router = APIRouter(prefix="/v1")

_STATUS_RE = re.compile(r"\bstatus=(\d{3})\b")


def get_search_service(request: Request) -> SearchService:
    service = getattr(request.app.state, "search_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Search service not ready")
    return service


def _upstream_status(exc: ProviderEndpointError) -> int:
    for attempt in exc.endpoint_attempts or []:
        code = attempt.get("status_code")
        if isinstance(code, int) and code >= 400:
            return code
        if attempt.get("reason") == "cooldown":
            return status.HTTP_503_SERVICE_UNAVAILABLE
        if attempt.get("reason") == "no_api_key":
            return status.HTTP_503_SERVICE_UNAVAILABLE
    match = _STATUS_RE.search(str(exc))
    if match:
        return int(match.group(1))
    if "cooling down" in str(exc).lower() or "not configured" in str(exc).lower():
        return status.HTTP_503_SERVICE_UNAVAILABLE
    return status.HTTP_502_BAD_GATEWAY


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def list_capabilities(
    _: str = Depends(require_api_key),
    service: SearchService = Depends(get_search_service),
) -> CapabilitiesResponse:
    return await service.capabilities()


@router.get("/usage")
async def usage(
    _: str = Depends(require_api_key),
    service: SearchService = Depends(get_search_service),
) -> dict:
    return await service.usage_snapshot()


@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    request: Request,
    api_key: str = Depends(require_api_key),
    service: SearchService = Depends(get_search_service),
) -> SearchResponse:
    cache = get_cache()
    allowed = await cache.check_rate_limit(api_key)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")
    hint = api_key[:4] if api_key != "anonymous" else "anon"
    return await service.search(body, api_key_hint=hint)


@router.post("/scrape", response_model=SearchResponse)
async def scrape(
    body: ScrapeRequest,
    api_key: str = Depends(require_api_key),
    service: SearchService = Depends(get_search_service),
) -> SearchResponse:
    cache = get_cache()
    allowed = await cache.check_rate_limit(api_key)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")
    hint = api_key[:4] if api_key != "anonymous" else "anon"
    return await service.scrape(body, api_key_hint=hint)


@router.post("/linkedin/profile", response_model=LinkedInProfileResponse)
async def linkedin_profile(
    body: LinkedInProfileRequest,
    api_key: str = Depends(require_api_key),
    service: SearchService = Depends(get_search_service),
) -> LinkedInProfileResponse:
    """Forced Scrapingdog LinkedIn Profile API (person or company). No SERP chain."""
    cache = get_cache()
    allowed = await cache.check_rate_limit(api_key)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")
    hint = api_key[:4] if api_key != "anonymous" else "anon"
    try:
        return await service.linkedin_profile(body, api_key_hint=hint)
    except LinkedInProfileValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ProviderEndpointError as exc:
        raise HTTPException(status_code=_upstream_status(exc), detail=str(exc)) from exc
