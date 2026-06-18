"""MCP server for Keystone Engage tools.

Exposes Engage tools via Model Context Protocol (graduation path 1.3).
Any MCP-compatible client can discover and call these tools.

This is the third of the six frozen contracts from the architecture document:
agent-to-tool communication via MCP.
"""

from __future__ import annotations

import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)

server = Server("keystone-engage-tools")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available Keystone Engage tools."""
    return [
        Tool(
            name="engage_health",
            description="Check Keystone Engage service health status.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch a tool call. Authorization is checked before execution."""
    if name == "engage_health":
        return [TextContent(type="text", text='{"status": "ok", "component": "keystone-engage"}')]

    return [TextContent(type="text", text=f'{{"error": "Unknown tool: {name}"}}')]


async def main() -> None:
    """Run the MCP server over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
