# MarketBrief

MarketBrief is a market-intelligence backend for AI assistants. This fork keeps the original data collection and optional Anthropic report-generation pipeline, but makes **remote MCP over Streamable HTTP** the primary deployment model.

The intended architecture is:

```text
ChatGPT / scheduled task
        |
        v
Streamable HTTP MCP
        |
        v
MarketBrief
  |- market data
  |- RSS/news
  |- economic calendar
  |- ETF flows
  |- optional regime/breadth skills
  `- optional embedded Anthropic report generation
```

This repository is a fork of `yukipanpan/marketbrief` and retains the upstream MIT licence.

## Remote MCP

Install with MCP support:

```bash
pip install -e ".[mcp]"
```

Run locally over Streamable HTTP:

```bash
python -m marketbrief.mcp_server --http 8080
```

Endpoints:

```text
GET  /health
MCP  /mcp
```

Local stdio remains available:

```bash
python -m marketbrief.mcp_server
```

Legacy SSE is retained for compatibility:

```bash
python -m marketbrief.mcp_server --sse 8080
```

## MCP tools

| Tool | Purpose | API key required |
|---|---|---:|
| `fetch_market_data` | Equities, commodities, FX, rates, volatility, optional crypto | No |
| `fetch_news` | Aggregate configured RSS feeds | No |
| `fetch_calendar` | Economic calendar and scheduled releases | No |
| `fetch_etf_flows` | ETF flow/AUM data | Optional, source-dependent |
| `analyze_regime` | Macro regime skill | No, currently optional/stub |
| `analyze_breadth` | Breadth skill | No, currently optional/stub |
| `generate_report` | Original embedded AI briefing pipeline | Anthropic unless `skip_ai=true` |

The primary remote use case does **not** require Anthropic. ChatGPT can call the data tools directly and perform the higher-level reasoning itself.

## Local test

Start the MCP server, then run:

```powershell
py .\Test-MarketBriefMcp.py
```

To test another endpoint, including Azure:

```powershell
$env:MARKETBRIEF_MCP_URL="https://your-host/mcp"
py .\Test-MarketBriefMcp.py
```

## Docker

Build:

```powershell
docker build -t marketbrief-mcp .
```

Run:

```powershell
docker run --rm -p 8080:8080 marketbrief-mcp
```

Test health:

```powershell
Invoke-RestMethod http://localhost:8080/health
```

The image listens on `0.0.0.0:8080`, exposes `/health` and `/mcp`, and requires no secrets to start.

## Azure Container Apps

The recommended deployment is an Azure Container App with:

- external HTTPS ingress
- target port `8080`
- minimum replicas `0`
- maximum replicas `1` initially
- `0.5` CPU / `1 GiB` memory initially
- `/health` for wake/health checks
- `/mcp` as the ChatGPT-facing MCP endpoint

A deployment helper is included:

```powershell
.\Deploy-MarketBriefAzure.ps1 -AcrName <your-existing-acr-name>
```

The script builds the image with ACR Tasks, creates the resource group/environment if needed, deploys or updates the Container App, enables scale-to-zero, and prints the public health and MCP URLs.

Current Azure CLI syntax is based on `az acr build`, `az containerapp create/update`, `az containerapp registry set`, and `az containerapp ingress enable`.

## Data sources

MarketBrief currently uses a mixture of free/public sources including Yahoo Finance, Frankfurter/ECB data, CoinGecko, RSS feeds, Forex Factory/MyFXBook-derived calendar data, and optional FRED/SoSoValue APIs.

Configuration lives under `config/`. If local config files are absent, the application falls back to the checked-in `*.example.json` files.

## Optional embedded AI

The original Anthropic two-stage pipeline is deliberately retained. It can still generate a standalone briefing when `ANTHROPIC_API_KEY` is supplied.

For this fork, however, embedded AI is optional rather than architectural: the preferred model is **MarketBrief as data/tool backend, ChatGPT as reasoning/orchestration layer**.

## Project structure

```text
marketbrief/
|- src/marketbrief/
|  |- core/
|  |- fetchers/
|  |- delivery/
|  |- renderers/
|  |- skills/
|  |- mcp_service.py      # MCP tools + transports
|  `- mcp_server.py       # module entry point
|- config/
|- Dockerfile
|- Deploy-MarketBriefAzure.ps1
|- Test-MarketBriefMcp.py
`- .github/workflows/streamable-http-smoke.yml
```

## Licence

MIT.
