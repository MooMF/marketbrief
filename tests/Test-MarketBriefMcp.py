import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    async with streamable_http_client(
        "http://127.0.0.1:8080/mcp"
    ) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()
            print("Server:", init.server_info.name)

            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            print("Tools:")
            for name in names:
                print(" -", name)

            expected = {
                "generate_report",
                "fetch_market_data",
                "fetch_news",
                "fetch_calendar",
                "analyze_regime",
                "analyze_breadth",
                "fetch_etf_flows",
            }
            missing = expected - set(names)
            if missing:
                raise SystemExit(f"Missing MCP tools: {sorted(missing)}")

            result = await session.call_tool(
                "fetch_market_data",
                {
                    "assets": ["S&P 500", "Gold"],
                    "include_crypto": False,
                },
            )

            payload = None
            for item in result.content:
                if hasattr(item, "text"):
                    payload = json.loads(item.text)
                    break

            if payload is None:
                raise SystemExit("fetch_market_data returned no text payload")

            prices = payload.get("prices", {})
            if set(prices) != {"S&P 500", "Gold"}:
                raise SystemExit(f"Unexpected prices: {sorted(prices)}")

            if payload.get("crypto") != []:
                raise SystemExit("include_crypto=False did not suppress crypto")

            text = payload.get("text", "")
            if "S&P 500" not in text or "Gold" not in text:
                raise SystemExit("Filtered text does not contain requested assets")
            if "Silver" in text or "WTI Crude" in text:
                raise SystemExit("Filtered text still contains unrequested assets")

            print("\nfetch_market_data contract: OK")
            print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
