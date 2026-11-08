from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.mcp_server import _RejectBareMcpGet


def _inner_app() -> Starlette:
    async def ok(_request):  # noqa: ANN001
        return PlainTextResponse("stream-ok")

    return Starlette(
        routes=[
            Route("/", ok, methods=["GET", "POST"]),
            Route("/{path:path}", ok, methods=["GET", "POST"]),
        ]
    )


def test_bare_mcp_get_rejected_without_session():
    app = _RejectBareMcpGet(_inner_app())
    with TestClient(app) as client:
        resp = client.get("/", headers={"Accept": "text/event-stream"})
    assert resp.status_code == 405
    assert resp.json()["error"] == "mcp_sse_requires_session"


def test_mcp_get_allowed_with_session_header():
    app = _RejectBareMcpGet(_inner_app())
    with TestClient(app) as client:
        resp = client.get("/", headers={"Mcp-Session-Id": "sess-1", "Accept": "text/event-stream"})
    assert resp.status_code == 200
    assert resp.text == "stream-ok"


def test_mcp_post_unaffected():
    app = _RejectBareMcpGet(_inner_app())
    with TestClient(app) as client:
        resp = client.post("/", content=b"{}")
    assert resp.status_code == 200
    assert resp.text == "stream-ok"
