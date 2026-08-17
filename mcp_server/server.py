"""MarketBrief MCP Server — expose market intelligence tools to AI assistants.

Run:
    python -m marketbrief.mcp_server                   # stdio (local clients)
    python -m marketbrief.mcp_server --http 8080       # Streamable HTTP (remote clients)
    python -m marketbrief.mcp_server --sse 8080        # legacy SSE compatibility

Install:
    pip install marketbrief[mcp]
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from marketbrief.core.config import MarketBriefConfig

log = logging.getLogger("marketbrief")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

server = Server("marketbrief")

# Global config — initialized once at startup
_cfg: MarketBriefConfig | None = None


def _get_cfg() -> MarketBriefConfig:
    global _cfg
    if _cfg is None:
        _cfg = MarketBriefConfig()
    return _cfg


# ── Tool Definitions ────────────────────────────────────────────────────────

TOOLS = [
    Tool(
        name="generate_report",
        description=(
            "Generate a full AI-powered market briefing report. "
            "Fetches market data, news, and calendar, then uses Claude to produce "
            "structured analysis with tagline, today's focus, 4-issue analysis, "
            "positioning table, news digest, and economic calendar. "
            "Requires ANTHROPIC_API_KEY."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "output_format": {
                    "type": "string",
                    "enum": ["json", "markdown", "html"],
                    "default": "json",
                    "description": "Output format for the report",
                },
                "skip_ai": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, return data-only report without AI analysis",
                },
            },
        },
    ),
    Tool(
        name="fetch_market_data",
        description=(
            "Fetch current market snapshot — equities, commodities, FX, rates, "
            "volatility, and crypto prices. Uses Yahoo Finance (primary) and "
            "Stooq (fallback). No API key required."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "assets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific asset labels to fetch (e.g. ['S&P 500', 'Gold']). Omit for all.",
                },
                "include_crypto": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include crypto prices from CoinGecko",
                },
            },
        },
    ),
    Tool(
        name="fetch_news",
        description=(
            "Fetch and aggregate news from 40+ RSS feeds covering macro, markets, "
            "crypto, AI/tech, geopolitics, and government sources. "
            "Returns deduplicated items with source, title, URL, and timestamp."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by category (e.g. ['crypto', 'macro', 'ai_tech']). Omit for all.",
                },
                "max_items": {
                    "type": "integer",
                    "default": 50,
                    "description": "Maximum number of items to return",
                },
                "hours": {
                    "type": "integer",
                    "default": 24,
                    "description": "Only return items from the last N hours",
                },
            },
        },
    ),
    Tool(
        name="fetch_calendar",
        description=(
            "Fetch economic calendar events from Forex Factory, MyFXBook, and FRED. "
            "Returns scheduled data releases, central bank decisions, and other "
            "market-moving events with times, impact levels, and actual values."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date to fetch (ISO format, e.g. '2026-04-07'). Defaults to today.",
                },
                "impact": {
                    "type": "string",
                    "enum": ["high", "medium", "low", "all"],
                    "default": "all",
                    "description": "Filter by impact level",
                },
            },
        },
    ),
    Tool(
        name="analyze_regime",
        description=(
            "Run the macro regime detector — identifies structural market regime "
            "shifts using cross-asset ratios (yield curve, credit conditions, "
            "equity-bond correlation, sector rotation, concentration). "
            "Returns regime classification and confidence scores."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "lookback_days": {
                    "type": "integer",
                    "default": 90,
                    "description": "Number of days of historical data to analyze",
                },
            },
        },
    ),
    Tool(
        name="analyze_breadth",
        description=(
            "Run the market breadth analyzer — measures market participation "
            "using advance/decline ratios, new highs/lows, moving average "
            "crossovers, and divergence signals."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="fetch_etf_flows",
        description=(
            "Fetch ETF flow and AUM data for BTC, ETH, and Gold spot ETFs. "
            "Uses SoSoValue API (primary) with RSS fallback."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "assets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Assets to fetch (e.g. ['BTC', 'ETH']). Defaults to all configured.",
                },
            },
        },
    ),
]


@server.list_tools()
async def list_tools():
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    cfg = _get_cfg()

    try:
        if name == "generate_report":
            result = _handle_generate_report(cfg, arguments)
        elif name == "fetch_market_data":
            result = _handle_fetch_market(cfg, arguments)
        elif name == "fetch_news":
            result = _handle_fetch_news(cfg, arguments)
        elif name == "fetch_calendar":
            result = _handle_fetch_calendar(cfg, arguments)
        elif name == "analyze_regime":
            result = _handle_analyze_regime(cfg, arguments)
        elif name == "analyze_breadth":
            result = _handle_analyze_breadth(cfg, arguments)
        elif name == "fetch_etf_flows":
            result = _handle_fetch_etf_flows(cfg, arguments)
        else:
            result = {"error": f"Unknown tool: {name}"}

        text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        return [TextContent(type="text", text=text)]

    except Exception as e:
        log.error(f"Tool {name} failed: {e}")
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


# ── Tool Handlers ────────────────────────────────────────────────────────────


def _handle_generate_report(cfg: MarketBriefConfig, args: dict) -> dict:
    from marketbrief.core.pipeline import run_pipeline

    return run_pipeline(
        config_dir=str(cfg.config_dir),
        output_format="json",
        skip_ai=args.get("skip_ai", False),
    )


def _handle_fetch_market(cfg: MarketBriefConfig, args: dict) -> dict:
    from marketbrief.fetchers.market import fetch_market_snapshot

    data = fetch_market_snapshot(cfg)
    requested = args.get("assets")
    if requested and isinstance(data, dict) and "prices" in data:
        data["prices"] = {
            k: v for k, v in data["prices"].items() if k in requested
        }
    return data


def _handle_fetch_news(cfg: MarketBriefConfig, args: dict) -> dict:
    from marketbrief.fetchers.news import fetch_news

    items = fetch_news(cfg)
    if not isinstance(items, list):
        return {"items": [], "count": 0}

    categories = args.get("categories")
    if categories:
        items = [i for i in items if i.get("category", "") in categories]

    hours = args.get("hours", 24)
    if hours:
        import time

        cutoff = time.time() - (hours * 3600)
        items = [i for i in items if i.get("published_at", 0) >= cutoff]

    max_items = args.get("max_items", 50)
    items = items[:max_items]

    return {"items": items, "count": len(items)}


def _handle_fetch_calendar(cfg: MarketBriefConfig, args: dict) -> dict:
    from marketbrief.fetchers.calendar import fetch_calendar

    events = fetch_calendar(cfg)
    if not isinstance(events, list):
        return {"events": [], "count": 0}

    impact = args.get("impact", "all")
    if impact != "all":
        events = [e for e in events if e.get("impact", "").lower() == impact]

    return {"events": events, "count": len(events)}


def _handle_analyze_regime(cfg: MarketBriefConfig, args: dict) -> dict:
    try:
        from marketbrief.skills.regime_detector import analyze

        return analyze(lookback_days=args.get("lookback_days", 90))
    except ImportError:
        return {"error": "Regime detector skill not yet ported", "status": "stub"}


def _handle_analyze_breadth(cfg: MarketBriefConfig, args: dict) -> dict:
    try:
        from marketbrief.skills.breadth_analyzer import analyze

        return analyze()
    except ImportError:
        return {"error": "Breadth analyzer skill not yet ported", "status": "stub"}


def _handle_fetch_etf_flows(cfg: MarketBriefConfig, args: dict) -> dict:
    from marketbrief.fetchers.etf_flows import fetch_etf_flows

    data = fetch_etf_flows(cfg)
    requested = args.get("assets")
    if requested and isinstance(data, dict):
        data = {k: v for k, v in data.items() if k in requested}
    return data


# ── Transports ───────────────────────────────────────────────────────────────


async def run_stdio():
    """Run MCP server over stdio transport."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def create_streamable_http_app() -> Starlette:
    """Build a stateless Streamable HTTP ASGI app.

    Stateless mode is deliberate: MarketBrief tools do not rely on MCP session
    state, and stateless requests are a better fit for container scale-to-zero
    and multiple replicas behind a load balancer.
    """
    session_manager = StreamableHTTPSessionManager(
        app=server,
        stateless=True,
        json_response=True,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with session_manager.run():
            yield

    async def health(request):
        return JSONResponse({"status": "ok", "service": "marketbrief-mcp"})

    return Starlette(
        routes=[
            Route("/health", endpoint=health, methods=["GET"]),
            Mount("/mcp", app=session_manager.handle_request),
        ],
        lifespan=lifespan,
    )


def run_streamable_http(port: int) -> None:
    """Run the production Streamable HTTP transport."""
    import uvicorn

    host = os.getenv("MCP_HOST", "0.0.0.0")
    log.info(f"Starting MarketBrief MCP server (Streamable HTTP) on {host}:{port}/mcp")
    uvicorn.run(create_streamable_http_app(), host=host, port=port)


def run_sse(port: int) -> None:
    """Run the legacy HTTP+SSE transport for compatibility."""
    from mcp.server.sse import SseServerTransport
    import uvicorn

    sse = SseServerTransport("/messages")

    async def handle_sse(request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages", endpoint=sse.handle_post_message, methods=["POST"]),
        ]
    )

    log.warning("SSE transport is legacy; prefer --http for new deployments")
    log.info(f"Starting MarketBrief MCP server (SSE) on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)


def _port_after(flag: str, default: int = 8080) -> int:
    """Read an optional integer port immediately following a CLI flag."""
    try:
        idx = sys.argv.index(flag) + 1
        return int(sys.argv[idx]) if idx < len(sys.argv) else default
    except (ValueError, IndexError):
        return default


def main():
    """CLI entry point for the MCP server."""
    if "--http" in sys.argv or "--streamable-http" in sys.argv:
        flag = "--http" if "--http" in sys.argv else "--streamable-http"
        run_streamable_http(_port_after(flag))
    elif "--sse" in sys.argv:
        run_sse(_port_after("--sse"))
    else:
        import asyncio

        asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
