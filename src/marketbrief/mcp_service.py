"""MarketBrief MCP service and transports."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from starlette.applications import Starlette
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route

from marketbrief.core.config import MarketBriefConfig

log = logging.getLogger("marketbrief")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_cfg: MarketBriefConfig | None = None


def _get_cfg() -> MarketBriefConfig:
    global _cfg
    if _cfg is None:
        _cfg = MarketBriefConfig()
    return _cfg


TOOLS = [
    Tool(
        name="generate_report",
        description=(
            "Generate a full AI-powered market briefing report. Fetches market data, "
            "news, and calendar, then uses the optional embedded Anthropic pipeline. "
            "Requires ANTHROPIC_API_KEY unless skip_ai=true."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "output_format": {
                    "type": "string",
                    "enum": ["json", "markdown", "html"],
                    "default": "json",
                },
                "skip_ai": {
                    "type": "boolean",
                    "default": False,
                    "description": "Return data-only report without embedded AI analysis",
                },
            },
        },
    ),
    Tool(
        name="fetch_market_data",
        description=(
            "Fetch current market snapshot: equities, commodities, FX, rates, "
            "volatility and optional crypto. No API key required."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "assets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific asset labels to return. Omit for all configured assets.",
                },
                "include_crypto": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include configured crypto prices from CoinGecko",
                },
            },
        },
    ),
    Tool(
        name="fetch_news",
        description=(
            "Fetch and aggregate news from configured RSS feeds covering macro, markets, "
            "crypto, AI/tech, geopolitics and government sources."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "categories": {"type": "array", "items": {"type": "string"}},
                "max_items": {"type": "integer", "default": 50},
                "hours": {"type": "integer", "default": 24},
            },
        },
    ),
    Tool(
        name="fetch_calendar",
        description="Fetch scheduled economic calendar events and market-moving releases.",
        inputSchema={
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "ISO date; defaults to today"},
                "impact": {
                    "type": "string",
                    "enum": ["high", "medium", "low", "all"],
                    "default": "all",
                },
            },
        },
    ),
    Tool(
        name="analyze_regime",
        description="Run the macro regime detector when the optional skill is available.",
        inputSchema={
            "type": "object",
            "properties": {"lookback_days": {"type": "integer", "default": 90}},
        },
    ),
    Tool(
        name="analyze_breadth",
        description="Run the market breadth analyzer when the optional skill is available.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="fetch_etf_flows",
        description="Fetch configured ETF flow and AUM data.",
        inputSchema={
            "type": "object",
            "properties": {"assets": {"type": "array", "items": {"type": "string"}}},
        },
    ),
]


async def list_tools(ctx, params) -> ListToolsResult:
    return ListToolsResult(tools=TOOLS)


async def call_tool(ctx, params) -> CallToolResult:
    name = params.name
    arguments = params.arguments or {}
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
        return CallToolResult(content=[TextContent(type="text", text=text)])
    except Exception as exc:
        log.exception("Tool %s failed", name)
        text = json.dumps({"error": str(exc)})
        return CallToolResult(content=[TextContent(type="text", text=text)], isError=True)


server = Server("marketbrief", on_list_tools=list_tools, on_call_tool=call_tool)


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
        requested_set = set(requested)
        data["prices"] = {k: v for k, v in data["prices"].items() if k in requested_set}
        lines = ["=== MARKET SNAPSHOT ==="]
        for item in data["prices"].values():
            close = item.get("close")
            change = item.get("chg_pct", 0)
            symbol = item.get("symbol", "")
            close_text = f"{close:.4f}".rstrip("0").rstrip(".") if isinstance(close, (int, float)) else str(close)
            lines.append(f"{item.get('label', '')} ({symbol}): {close_text} {change:+.1f}%")
        data["text"] = "\n".join(lines)

    if args.get("include_crypto", True):
        from marketbrief.fetchers.crypto import fetch_crypto

        crypto = fetch_crypto(cfg)
        if requested:
            requested_upper = {str(a).upper() for a in requested}
            crypto = [
                coin for coin in crypto
                if str(coin.get("symbol", "")).upper() in requested_upper
                or str(coin.get("name", "")).upper() in requested_upper
            ]
        data["crypto"] = crypto
    else:
        data["crypto"] = []

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

    items = items[: args.get("max_items", 50)]
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


async def run_stdio() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def create_streamable_http_app() -> Starlette:
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

    async def mcp_redirect(request):
        return RedirectResponse(url="/mcp/", status_code=307)

    return Starlette(
        routes=[
            Route("/health", endpoint=health, methods=["GET"]),
            Route("/mcp", endpoint=mcp_redirect, methods=["GET", "POST", "DELETE"]),
            Mount("/mcp/", app=session_manager.handle_request),
        ],
        lifespan=lifespan,
    )


def run_streamable_http(port: int) -> None:
    import uvicorn

    host = os.getenv("MCP_HOST", "0.0.0.0")
    log.info("Starting MarketBrief Streamable HTTP on %s:%d/mcp/", host, port)
    uvicorn.run(create_streamable_http_app(), host=host, port=port)


def run_sse(port: int) -> None:
    """Legacy SSE compatibility transport."""
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
    log.warning("SSE is legacy; prefer --http")
    uvicorn.run(app, host=os.getenv("MCP_HOST", "0.0.0.0"), port=port)


def _port_after(flag: str, default: int = 8080) -> int:
    try:
        idx = sys.argv.index(flag) + 1
        return int(sys.argv[idx]) if idx < len(sys.argv) else default
    except (ValueError, IndexError):
        return default


def main() -> None:
    if "--http" in sys.argv or "--streamable-http" in sys.argv:
        flag = "--http" if "--http" in sys.argv else "--streamable-http"
        run_streamable_http(_port_after(flag))
    elif "--sse" in sys.argv:
        run_sse(_port_after("--sse"))
    else:
        import asyncio
        asyncio.run(run_stdio())
