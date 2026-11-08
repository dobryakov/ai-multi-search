# ai-multi-search

Capability-based multi-provider internet search microservice with HTTP API and MCP.

## Features

- Extensible capabilities: `web_search`, `person_search`, `company_search`, `scrape`, `search_and_scrape`
- Intent routing: cheap LLM (OpenAI-compatible) with soft heuristic fallback
- Optional/auto **structured entity extraction** (person/company) via a cheap OpenRouter chat model
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

Compose also starts a **private SearXNG** (`ai-multi-search-searxng`, config in `deploy/searxng/`) on the internal network. API uses `SEARXNG_URL=http://searxng:8080` by default; JSON search format is enabled. No host port is published.

Local debug port defaults to `127.0.0.1:18100` (container still listens on `8000` internally).

## HTTP API

All `/v1/*` require `Authorization: Bearer <key>` when `API_KEYS` is set.

- `POST /v1/search` — `{ "query": "...", "type?": "web_search|person_search|...", "limit": 10, "structured?": true|false|null }`
- `POST /v1/scrape` — `{ "url": "https://...", "formats": ["markdown"] }`
- `GET /v1/capabilities`
- `GET /v1/usage` — provider counters + OpenRouter cost (all-time and today UTC)
- `GET /health` (no auth)
- `GET /ready`

### Structured response (`structured`)

After providers return hits, a cheap OpenRouter model can normalize them into a single entity object on `structured`:

| `structured` | Behavior |
|---|---|
| omitted / `null` | **auto**: on for `person_search` / `company_search`, off otherwise |
| `true` | force (works for web too) |
| `false` | never run |

Model: `STRUCTURE_MODEL` (default `google/gemini-2.5-flash-lite`). Uses the same `OPENROUTER_API_KEY`. Failure is soft — search hits still return; see `meta.structure_error`.

Example person payload:

```json
{
  "structured": {
    "kind": "person",
    "name": "Grigoriy Dobryakov",
    "headline": "Head of AI Engineering",
    "organization": "",
    "role": "Head of AI Engineering",
    "location": "",
    "linkedin_url": "",
    "website": "https://www.dobryakov.net/",
    "summary": "...",
    "confidence": 0.82,
    "evidence_urls": ["https://www.dobryakov.net/"]
  }
}
```

Company uses `kind: "company"` (`name`, `domain`, `industry`, `headquarters`, `linkedin_url`, …). OpenRouter intent + structure costs are summed in `usage.openrouter` (`parts` lists each call).

## MCP

Streamable HTTP endpoint: `{PUBLIC_BASE_URL}/mcp`  
Local: `http://127.0.0.1:18100/mcp`

Tools: `search`, `search_web`, `search_person`, `search_company`, `scrape`, `list_capabilities`, `get_usage`

Each search/scrape/MCP tool response includes a top-level `usage` block for **this request**:

```json
{
  "usage": {
    "cache_hit": false,
    "latency_ms": 1234.5,
    "provider_used": "serper_person",
    "providers": [
      {"provider": "serper_person", "outcome": "ok", "latency_ms": 1050.1}
    ],
    "openrouter": {
      "model": "typesafe/jev-1.14-...",
      "ok": true,
      "input_tokens": 430,
      "output_tokens": 62,
      "cost_usd": 0.000018
    },
    "cost_usd": 0.000018
  }
}
```

`cost_usd` is known billed USD for the request (OpenRouter/Jev). Provider calls are listed in `providers[]` even when the upstream API does not return a dollar amount. Aggregates: `GET /v1/usage` / MCP `get_usage`.

### Provider cooldown

After `401/402/403/429` (or quota/auth-like error text), a provider is skipped for `PROVIDER_COOLDOWN_SECONDS` (default 900). Status appears in `GET /v1/capabilities` (`cooldown`, `cooldown_remaining_seconds`, `cooldown_reason`) and in per-request `usage.providers[].outcome = "skipped_cooldown"`.

### Scrapingdog Google (last resort)

`scrapingdog_google` (`/google`) is registered only as the **last** `web_search` provider.  
Person/company flows try specialized providers first, then the rest of `web_search`, and only then Scrapingdog Google. It is never used as an early internal fallback from LinkedIn people/company search.

`scrapingdog_people` calls LinkedIn people search only when the query parses to a real person name (skips role queries like “who is the CTO of …”).  
`scrapingdog_company` uses Profile API `GET /profile/?type=company&id=<linkedin_slug>` (docs example is by LinkedIn universal name like `amazon`, **not** free-text name search). Expensive (~50 credits) — runs only after cheaper company/web providers, just before `scrapingdog_google`. Skips when no slug/URL can be derived.

`search_and_scrape` is a composite of available `web_search` + `scrape` providers (no dedicated provider class).

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
