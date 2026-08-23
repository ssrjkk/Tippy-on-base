"""MCP Server — exposes Tippy tools for external agents.

External agents can tip, bet, buy paywall items via MCP protocol.
Requires: pip install mcp

Usage:
    python -m agent.mcp_server              # stdio transport
    python -m agent.mcp_server --sse 8081   # SSE transport on port 8081
"""

import asyncio
import json
import os
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from . import config
from .tools import create_market, place_bet, get_market, list_open_markets, get_balance

server = Server("tippy-agent")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="tippy_create_market",
            description="Create a prediction market on Tippy (Base). Subsidized with LMSR AMM.",
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Market question (e.g. 'Will ETH hit $5000 by Dec 2026?')",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 4,
                        "description": "Outcome options (max 4, 64 chars each)",
                    },
                    "hours": {
                        "type": "number",
                        "description": "Market duration in hours (1-168)",
                        "default": 24,
                    },
                    "subsidy_usdc": {
                        "type": "number",
                        "description": "LMSR subsidy in USDC (min $10)",
                        "default": 10,
                    },
                },
                "required": ["question", "options"],
            },
        ),
        Tool(
            name="tippy_place_bet",
            description="Place a bet on an existing prediction market. Returns new balance.",
            inputSchema={
                "type": "object",
                "properties": {
                    "market_id": {"type": "integer", "description": "Market ID"},
                    "outcome_idx": {
                        "type": "integer",
                        "description": "Outcome index (0-based)",
                    },
                    "amount_usdc": {
                        "type": "number",
                        "description": "Bet amount in USDC",
                    },
                },
                "required": ["market_id", "outcome_idx", "amount_usdc"],
            },
        ),
        Tool(
            name="tippy_get_market",
            description="Get market details including live LMSR odds.",
            inputSchema={
                "type": "object",
                "properties": {
                    "market_id": {"type": "integer", "description": "Market ID"},
                },
                "required": ["market_id"],
            },
        ),
        Tool(
            name="tippy_list_markets",
            description="List open prediction markets with current odds.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max markets to return",
                        "default": 10,
                    },
                },
            },
        ),
        Tool(
            name="tippy_get_balance",
            description="Get agent's USDC balance on Tippy.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "tippy_create_market":
            result = await create_market(
                question=arguments["question"],
                options=arguments["options"],
                hours=arguments.get("hours", 24),
                subsidy_usdc=arguments.get("subsidy_usdc", 10),
            )
        elif name == "tippy_place_bet":
            result = await place_bet(
                market_id=arguments["market_id"],
                outcome_idx=arguments["outcome_idx"],
                amount_usdc=arguments["amount_usdc"],
            )
        elif name == "tippy_get_market":
            result = await get_market(arguments["market_id"])
            if result is None:
                result = {"error": "Market not found"}
        elif name == "tippy_list_markets":
            result = await list_open_markets(arguments.get("limit", 10))
        elif name == "tippy_get_balance":
            bal = await get_balance()
            result = {"balance_usdc": bal}
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, default=str))]

    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def _run_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def _run_sse(port: int):
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )

    import uvicorn
    config_uvicorn = uvicorn.Config(app, host="0.0.0.0", port=port)
    srv = uvicorn.Server(config_uvicorn)
    await srv.serve()


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sse", type=int, metavar="PORT", help="Run as SSE server on given port")
    args = ap.parse_args()

    if args.sse:
        asyncio.run(_run_sse(args.sse))
    else:
        asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
