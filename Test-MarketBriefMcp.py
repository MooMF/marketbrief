"""Local integration test for the MarketBrief Streamable HTTP MCP endpoint."""

import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


MCP_URL = os.environ.get("MARKETBRIEF_MCP_URL", "http://127.0.0.1:8080/mcp")


async def main() -> None:
    async with streamable_http_client(MCP_URL) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()
            print("Server:", init.server_info.name)

            tools = await session.list_tools()
            print("Tools:")
            for tool in tools.tools:
                print(" -", tool.name)

            print("\nCalling fetch_market_data...")
            result = await session.call_tool(
                "fetch_market_data",
                {"assets": ["S&P 500", "Gold"], "include_crypto": False},
            )

            print("\nRaw result:")
            for item in result.content:
                text = getattr(item, "text", None)
                if text is None:
                    print(item)
                    continue
                try:
                    print(json.dumps(json.loads(text), indent=2))
                except json.JSONDecodeError:
                    print(text)


if __name__ == "__main__":
    asyncio.run(main())
