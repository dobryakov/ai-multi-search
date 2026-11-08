from __future__ import annotations

import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import router as api_router
from app.cache.redis import Cache, get_cache, set_cache
from app.config import get_settings
from app.mcp_server import bind_search_service, create_mcp, mcp_asgi_app
from app.providers.manager import build_default_manager
from app.services.search import SearchService
from app.usage.tracker import UsageTracker, set_usage_tracker

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    mcp = create_mcp()
    mcp_mount = mcp_asgi_app(mcp)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

        cache = Cache(settings)
        await cache.connect()
        set_cache(cache)

        usage = UsageTracker(cache=cache, settings=settings)
        set_usage_tracker(usage)

        manager = build_default_manager(settings, usage=usage)
        service = SearchService(manager=manager, cache=cache, usage=usage, settings=settings)
        app.state.search_service = service
        bind_search_service(service)

        logger.info(
            "ai-multi-search ready; public_base_url=%s available_caps=%s",
            settings.public_base_url,
            [c.name for c in service.capabilities().capabilities if c.available],
        )

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(mcp.session_manager.run())
            try:
                yield
            finally:
                await cache.close()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    app.include_router(api_router)
    app.mount("/mcp", mcp_mount)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        cache = get_cache()
        service = getattr(request.app.state, "search_service", None)
        payload = {
            "status": "ready" if service else "not_ready",
            "redis": cache._redis is not None,
            "llm_configured": settings.llm_configured(),
            "llm_model": settings.llm_model,
            "capabilities": service.capabilities().model_dump() if service else None,
        }
        code = 200 if service else 503
        return JSONResponse(payload, status_code=code)

    return app


app = create_app()
