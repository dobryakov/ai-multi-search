# ai-multi-search

Capability-based multi-provider internet search microservice with HTTP API and MCP.

## Features

- Extensible capabilities: `web_search`, `person_search`, `company_search`, `scrape`, `search_and_scrape`, plus forced `linkedin_profile`
- Intent routing: cheap LLM (OpenAI-compatible) with soft heuristic fallback
- Optional/auto **structured entity extraction** (person/company) via a cheap OpenRouter chat model
- Opt-in **company enrich** (`enrich: true`): sequential engage-research facets (culture/hiring/funding/vendors/initiatives)
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

- `POST /v1/search` — `{ "query": "...", "type?": "web_search|person_search|...", "limit": 10, "structured?": true|false|null, "enrich?": false, "enrich_facets?": ["funding", "vendors"], "enrich_context?": "software" }`
- `POST /v1/scrape` — `{ "url": "https://...", "formats": ["markdown"] }`
- `POST /v1/linkedin/profile` — forced Scrapingdog Profile API (see below)
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

### Company enrich (`enrich`)

Opt-in engage research for `company_search` only (`enrich: true`, default **false**). The primary lookup becomes a funding/news-first web SERP (`"{company}" company news OR funding OR acquisition`); `enrich_context` adds a quoted industry hint for short or ambiguous names. It then runs fixed EN facet SERPs **sequentially** (short delay between calls) for:

| facet | focus |
|---|---|
| `culture` | culture / values / WLB |
| `hiring` | interview / careers |
| `funding` | funding / investment / valuation |
| `vendors` | contractors / consultants / professional services |
| `initiatives` | RFP / procurement / transformation / consulting engagements |

`enrich_facets` optionally limits the fan-out; omitted means all five facets. `limit` applies only to the primary lookup; each facet uses `ENRICH_FACET_LIMIT` (default 3). Merged `results` may exceed `limit`; primary funding/news hits and facet hits carry `extra.facet`. Structure extraction gets extra fields (`culture`, `hiring`, `funding`, `vendor_engagement`, `initiatives`, `pros`/`cons`, …). See `meta.enrich`, `meta.facets`, and `meta.enrich_primary_query`.

## MCP

Streamable HTTP endpoint: `{PUBLIC_BASE_URL}/mcp`  
Local: `http://127.0.0.1:18100/mcp`

Tools: `search`, `search_web`, `search_person`, `search_company`, `scrape`, `linkedin_profile`, `list_capabilities`, `get_usage`

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

After provider-API `401/402/429` (or quota/auth-like error text), a provider is skipped for `PROVIDER_COOLDOWN_SECONDS` (default 900). **`403` is not a cooldown** — for scrapers like Trafilatura that is usually the *target page*, not provider auth.

Upstream `5xx` on paid search endpoints (e.g. Scrapingdog Google HTML 500) also cools that provider so every `person_search` does not keep hitting a dead hop.

Status appears in `GET /v1/capabilities` (`cooldown`, `cooldown_remaining_seconds`, `cooldown_reason`) and in per-request `usage.providers[].outcome = "skipped_cooldown"`.

### Scrapingdog Google (last resort)

`scrapingdog_google` (`/google`) is registered only as the **last** `web_search` provider.  
Person/company flows try specialized providers first, then the rest of `web_search`, and only then Scrapingdog Google. It is never used as an early internal fallback from LinkedIn people/company search.

**People by name:** Scrapingdog has no people-by-name search (`/linkedin/search?type=people` is broken upstream). `person_search` with a free-text name runs the normal cheap search chain (`serper_person` / SearXNG / Serper / … / last-resort `scrapingdog_google`) with `site:linkedin.com/in` appended — **not** Scrapingdog Google.

**People by LinkedIn id (expensive):** `scrapingdog_people` uses Profile API `GET /profile/?type=profile&id=<linkedin_id>` when the query is a LinkedIn person URL (`linkedin.com/in/rbranson`) or a bare vanity id (`rbranson`). **Cost: 50–100 Scrapingdog credits per successful request.** Free-text names skip this provider with no HTTP call. Prefer SERP discovery first; use Profile only when you already have the id and need full profile JSON.

`scrapingdog_company` uses Profile API `GET /profile/?type=company&id=<linkedin_slug>` (docs example is by LinkedIn universal name like `amazon`, **not** free-text name search). Expensive (~50 credits) — runs only after cheaper company/web providers, just before `scrapingdog_google`. Skips when no slug/URL can be derived.

Both Profile providers send `premium=true` when `SCRAPINGDOG_LINKEDIN_PREMIUM` is on (default **true**).

### Forced LinkedIn Profile (`POST /v1/linkedin/profile`)

Use this when you already have a LinkedIn vanity id or URL and need the **full Scrapingdog Profile JSON** (`experience`, `activities`, company roster/geo, …) — not SERP search hits. MCP mirror: `linkedin_profile`.

| | |
|---|---|
| Path | `POST /v1/linkedin/profile` |
| Upstream | **only** Scrapingdog `GET /profile/?type=profile\|company&id=…` |
| No | Serper / SearXNG / Firecrawl / search fallback chain |
| Auth | same bearer as other `/v1/*` |
| Env | `SCRAPINGDOG_API_KEY`, `SCRAPINGDOG_LINKEDIN_PREMIUM` (default `true`) |

**Request** — provide exactly one of `id` or `url` (`type` required):

```json
{ "type": "person", "id": "dobryakov" }
```

```json
{ "type": "company", "url": "https://www.linkedin.com/company/jetbrains" }
```

Optional `premium: true|false` overrides the env default. Free-text (`"JetBrains CEO"`, `"who is X"`) → **400** (this is not a search endpoint).

**Response** — lossless upstream JSON in `profile`:

```json
{
  "type": "person",
  "id": "dobryakov",
  "url": "https://www.linkedin.com/in/dobryakov",
  "provider_used": "scrapingdog_profile",
  "premium": true,
  "profile": { "experience": [], "activities": [], "location": "...", "public_identifier": "dobryakov" },
  "usage": {
    "provider_used": "scrapingdog_profile",
    "providers": [{ "provider": "scrapingdog_profile", "outcome": "ok", "latency_ms": 1200.0 }]
  }
}
```

Upstream HTTP errors are propagated (status + detail). Cooldown is shared with `scrapingdog_people` / `scrapingdog_company`. `person_search` / `company_search` stay unchanged (SERP + optional Profile inside the chain).

```bash
curl -s -X POST http://127.0.0.1:18100/v1/linkedin/profile \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"type":"person","id":"dobryakov"}'
```

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
