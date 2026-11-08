from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.types import ASGIApp, Receive, Scope, Send

from app.normalize.schema import LinkedInProfileRequest, ScrapeRequest, SearchRequest
from app.providers.errors import ProviderEndpointError
from app.providers.scrapingdog_profile import LinkedInProfileValidationError
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
            "or search_web/search_person/search_company for explicit capability routing. "
            "Use linkedin_profile when you already have a LinkedIn vanity id or URL and "
            "need the full Scrapingdog Profile JSON (no SERP fallback)."
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
        structured: bool | None = None,
    ) -> dict[str, Any]:
        """Search the internet. Intent is classified automatically (LLM then heuristics).

        structured: null=auto (on for person/company), true/false to force.
        """
        resp = await _svc().search(
            SearchRequest(
                query=query,
                limit=limit,
                language=language,
                country=country,
                structured=structured,
            ),
            api_key_hint="mcp",
        )
        return resp.model_dump(mode="json")

    @server.tool()
    async def search_web(
        query: str,
        limit: int = 10,
        language: str | None = None,
        country: str | None = None,
        structured: bool | None = None,
    ) -> dict[str, Any]:
        """Explicit web search (skips LLM intent classification)."""
        resp = await _svc().search(
            SearchRequest(
                query=query,
                type="web_search",
                limit=limit,
                language=language,
                country=country,
                structured=structured,
            ),
            api_key_hint="mcp",
        )
        return resp.model_dump(mode="json")

    @server.tool()
    async def search_person(
        query: str,
        limit: int = 10,
        structured: bool | None = None,
    ) -> dict[str, Any]:
        """Search for a person (structured entity extraction on by default)."""
        resp = await _svc().search(
            SearchRequest(
                query=query,
                type="person_search",
                limit=limit,
                structured=structured,
            ),
            api_key_hint="mcp",
        )
        return resp.model_dump(mode="json")

    @server.tool()
    async def search_company(
        query: str,
        limit: int = 10,
        structured: bool | None = None,
    ) -> dict[str, Any]:
        """Search for a company/organization (structured entity extraction on by default)."""
        resp = await _svc().search(
            SearchRequest(
                query=query,
                type="company_search",
                limit=limit,
                structured=structured,
            ),
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
    async def linkedin_profile(
        type: str,
        id: str | None = None,
        url: str | None = None,
        premium: bool | None = None,
    ) -> dict[str, Any]:
        """Fetch full LinkedIn person/company JSON via Scrapingdog Profile API only.

        type: "person" or "company". Provide exactly one of id (vanity slug) or url.
        No SERP / Firecrawl / fallback. Free-text names are rejected.
        """
        if type not in {"person", "company"}:
            return {
                "error": "invalid_type",
                "message": 'type must be "person" or "company"',
            }
        try:
            resp = await _svc().linkedin_profile(
                LinkedInProfileRequest(type=type, id=id, url=url, premium=premium),  # type: ignore[arg-type]
                api_key_hint="mcp",
            )
            return resp.model_dump(mode="json")
        except LinkedInProfileValidationError as exc:
            return {"error": "validation_error", "message": str(exc), "status": 400}
        except ProviderEndpointError as exc:
            return {"error": "provider_error", "message": str(exc)}

    @server.tool()
    async def list_capabilities() -> dict[str, Any]:
        """List currently available search capabilities and providers."""
        return (await _svc().capabilities()).model_dump(mode="json")

    @server.tool()
    async def get_usage() -> dict[str, Any]:
        """Provider call counters and OpenRouter Jev cost totals (Redis-backed)."""
        return await _svc().usage_snapshot()

    return server


class _RejectBareMcpGet:
    """Reject session-less GET (SSE) so clients do not hang on an empty stream.

    Streamable HTTP uses GET for an established session's event stream. A bare
    GET without ``Mcp-Session-Id`` previously returned 200 + endless empty SSE.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("method") == "GET":
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in (scope.get("headers") or [])
            }
            session = headers.get("mcp-session-id") or headers.get("x-mcp-session-id")
            if not session:
                body = json.dumps(
                    {
                        "error": "mcp_sse_requires_session",
                        "message": (
                            "GET /mcp opens an SSE stream only with an active "
                            "Mcp-Session-Id. Initialize via POST first."
                        ),
                    }
                ).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 405,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii")),
                            (b"allow", b"POST, DELETE"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def mcp_asgi_app(mcp: FastMCP) -> ASGIApp:
    """HTTP mount without nested lifespan (parent FastAPI owns session_manager.run())."""
    # Initialize session manager via FastMCP helper, then remount routes without lifespan.
    wrapped = mcp.streamable_http_app()
    inner = Starlette(routes=list(wrapped.routes), middleware=list(wrapped.user_middleware))
    return _RejectBareMcpGet(inner)