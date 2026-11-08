from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.api_keys import require_api_key
from app.cache.redis import get_cache
from app.normalize.schema import (
    CapabilitiesResponse,
    ScrapeRequest,
    SearchRequest,
    SearchResponse,
)
from app.services.search import SearchService

router = APIRouter(prefix="/v1")


def get_search_service(request: Request) -> SearchService:
    service = getattr(request.app.state, "search_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Search service not ready")
    return service


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def list_capabilities(
    _: str = Depends(require_api_key),
    service: SearchService = Depends(get_search_service),
) -> CapabilitiesResponse:
    return service.capabilities()


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
