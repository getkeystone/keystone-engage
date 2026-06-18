"""Placeholder tool for the Keystone Engage MCP catalog.

Replace with real engagement tools as the behavioral content library
and dialog management are built out.
"""

from __future__ import annotations


async def hello_tool() -> dict:
    """Health check tool exposed via MCP."""
    return {
        "status": "ok",
        "component": "keystone-engage",
        "platform": "keystone",
    }
