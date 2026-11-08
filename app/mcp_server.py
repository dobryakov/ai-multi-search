from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route

from app.normalize.schema import ScrapeRequest, SearchRequest
from app.services.search import SearchService

_service: SearchService | None = None


def bind_search_service(service: SearchService) -> None:
    global _service
    _service = service


def _svc() -> SearchService:
    if _service is None:
        raise RuntimeError("Search service is not bound to MCP")
    return _service


def create_mcp() -> FastMCP:
    """Fresh FastMCP instance (session manager can only .run() once per instance)."""
    server = FastMCP(
        "ai-multi-search",
        instructions=(
            "Multi-provider internet search. Use search for free-text intents, "
            "or search_web/search_person/search_company for explicit capability routing."
        ),
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
    )

    @server.tool()
    async def search(
        query: str,
        limit: int = 10,
        language: str | None = None,
        country: str | None = None,
    ) -> dict[str, Any]:
        """Search the internet. Intent is classified automatically (LLM then heuristics)."""
        resp = await _svc().search(
            SearchRequest(query=query, limit=limit, language=language, country=country),
            api_key_hint="mcp",
        )
        return resp.model_dump(mode="json")

    @server.tool()
    async def search_web(
        query: str,
        limit: int = 10,
        language: str | None = None,
        country: str | None = None,
    ) -> dict[str, Any]:
        """Explicit web search (skips LLM intent classification)."""
        resp = await _svc().search(
            SearchRequest(
                query=query,
                type="web_search",
                limit=limit,
                language=language,
                country=country,
            ),
            api_key_hint="mcp",
        )
        return resp.model_dump(mode="json")

    @server.tool()
    async def search_person(query: str, limit: int = 10) -> dict[str, Any]:
        """Search for a person."""
        resp = await _svc().search(
            SearchRequest(query=query, type="person_search", limit=limit),
            api_key_hint="mcp",
        )
        return resp.model_dump(mode="json")

    @server.tool()
    async def search_company(query: str, limit: int = 10) -> dict[str, Any]:
        """Search for a company/organization."""
        resp = await _svc().search(
            SearchRequest(query=query, type="company_search", limit=limit),
            api_key_hint="mcp",
        )
        return resp.model_dump(mode="json")

    @server.tool()
    async def scrape(url: str, formats: list[str] | None = None) -> dict[str, Any]:
        """Scrape a single public URL into markdown/text."""
        resp = await _svc().scrape(
            ScrapeRequest(url=url, formats=formats or ["markdown"]),  # type: ignore[arg-type]
            api_key_hint="mcp",
        )
        return resp.model_dump(mode="json")

    @server.tool()
    async def list_capabilities() -> dict[str, Any]:
        """List currently available search capabilities and providers."""
        return (await _svc().capabilities()).model_dump(mode="json")

    @server.tool()
    async def get_usage() -> dict[str, Any]:
        """Provider call counters and OpenRouter Jev cost totals (Redis-backed)."""
        return await _svc().usage_snapshot()

    return server


def mcp_asgi_app(mcp: FastMCP) -> Starlette:
    """HTTP mount without nested lifespan (parent FastAPI owns session_manager.run())."""
    # Initialize session manager via FastMCP helper, then remount routes without lifespan.
    wrapped = mcp.streamable_http_app()
    return Starlette(routes=list(wrapped.routes), middleware=list(wrapped.user_middleware))
