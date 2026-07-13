"""Minimal real MCP server, used only by tests/test_mcp.py's live smoke
test - not part of the shipped app. Exposes two tools so the test can
verify a genuine subprocess round-trip (list_tools + call_tool) through the
real `mcp` SDK, not a mock:

  - echo(text): returns text.upper() - proves args flow in and results flow
    back correctly.
  - fail(): always returns an MCP error result - proves McpSkillAdapter
    surfaces tool-reported errors as SkillResult(ok=False), not an exception.

Run directly (`python tests/fixtures/echo_mcp_server.py`) and it speaks MCP
over stdio, same as any other MCP server Yagami would connect to.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("yagami-test-echo-server")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the input text back, uppercased."""
    return text.upper()


@mcp.tool()
def fail() -> str:
    """Always raises - FastMCP turns this into an MCP tool error result."""
    raise ValueError("intentional failure for testing")


if __name__ == "__main__":
    mcp.run()
