"""Tiny stdio MCP server used by tests: echo, add, boom (tool error), slow."""
import asyncio
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

server = MCPServer("stub")


@server.tool()
def echo(text: str) -> str:
    """Return the text unchanged."""
    return text


@server.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@server.tool()
def boom() -> str:
    """Always fails."""
    raise RuntimeError("kaboom")


@server.tool()
async def slow() -> str:
    """Sleeps longer than the test timeout."""
    await asyncio.sleep(60)
    return "late"


if __name__ == "__main__":
    server.run("stdio")
