"""Agent-style multi-tool smoke test for the MarketBrief MCP endpoint.

This is deliberately model-free: it proves that a remote client can initialize MCP,
discover tools, and make several dependent market-intelligence calls in one session.
Set MARKETBRIEF_MCP_URL to test Azure; otherwise localhost is used.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


MCP_URL = os.environ.get("MARKETBRIEF_MCP_URL", "http://127.0.0.1:8080/mcp/")


def _payload(result: Any) -> Any:
    if not result.content:
        return None
    text = getattr(result.content[0], "text", None)
    if text is None:
        return result.content
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


async def main() -> None:
    print(f"Endpoint: {MCP_URL}")

    async with streamable_http_client(MCP_URL) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()
            print(f"Server: {init.server_info.name}")

            tools = await session.list_tools()
            available = {tool.name for tool in tools.tools}
            print(f"Available tools: {', '.join(sorted(available))}")

            print("\n[1/3] Fetching cross-asset market snapshot...")
            market = _payload(await session.call_tool(
                "fetch_market_data",
                {"include_crypto": True},
            ))

            print("[2/3] Fetching recent macro/markets/AI news...")
            news = _payload(await session.call_tool(
                "fetch_news",
                {
                    "categories": ["macro", "markets", "ai_tech", "geopolitics"],
                    "hours": 24,
                    "max_items": 20,
                },
            ))

            print("[3/3] Fetching economic calendar...")
            calendar = _payload(await session.call_tool(
                "fetch_calendar",
                {"impact": "high"},
            ))

            summary = {
                "server": init.server_info.name,
                "market": market,
                "news": news,
                "calendar": calendar,
            }

            print("\n=== AGENTIC MCP RESULT ===")
            print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
