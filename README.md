# ai-multi-search

Capability-based multi-provider internet search microservice with HTTP API and MCP.

## Features

- Extensible capabilities: `web_search`, `person_search`, `company_search`, `scrape`, `search_and_scrape`
- Intent routing: cheap LLM (OpenAI-compatible) with soft heuristic fallback
- Dynamic provider selection from currently available keys/URLs
- Providers: SearXNG, Brave, Serper, SerpAPI, Tavily, Firecrawl, Scrapingdog, Trafilatura, Crawl4AI
- Bearer API-key auth, Redis cache, request logs to stdout
- MCP tools mirrored from HTTP

## Quick start (Docker)

```bash
cp .env.example .env
# fill API_KEYS, provider keys, PUBLIC_BASE_URL, CADDY_DOCKER_NETWORK
docker compose up -d --build
curl -s http://127.0.0.1:18100/health
```

Local debug port defaults to `127.0.0.1:18100` (container still listens on `8000` internally).

## HTTP API

All `/v1/*` require `Authorization: Bearer <key>` when `API_KEYS` is set.

- `POST /v1/search` — `{ "query": "...", "type?": "web_search|person_search|...", "limit": 10 }`
- `POST /v1/scrape` — `{ "url": "https://...", "formats": ["markdown"] }`
- `GET /v1/capabilities`
- `GET /health` (no auth)
- `GET /ready`

## MCP

Streamable HTTP endpoint: `{PUBLIC_BASE_URL}/mcp`  
Local: `http://127.0.0.1:18100/mcp`

Tools: `search`, `search_web`, `search_person`, `search_company`, `scrape`, `list_capabilities`

### Cursor (`~/.cursor/mcp.json` or project `.cursor/mcp.json`)

Local debug:

```json
{
  "mcpServers": {
    "ai-multi-search": {
      "url": "http://127.0.0.1:18100/mcp"
    }
  }
}
```

Through a public reverse proxy:

```json
{
  "mcpServers": {
    "ai-multi-search": {
      "url": "https://search.example.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

Replace `search.example.com` / `YOUR_API_KEY` with your `PUBLIC_BASE_URL` host and a key from `API_KEYS`.  
HTTP `/v1/*` always checks the bearer key when `API_KEYS` is set; put the same header on MCP if your proxy or future auth middleware requires it.

## Reverse proxy checklist

1. Point DNS for your public hostname at the server
2. Attach the API container to your proxy Docker network via `CADDY_DOCKER_NETWORK` in `.env`
3. Add the site block from `docs/caddy-snippet.caddy` (replace `search.example.com`)
4. Reload the reverse proxy
5. Verify `https://<your-host>/health`

## Local tests

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```
