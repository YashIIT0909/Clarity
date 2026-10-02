"""Prompt 1 spike: one UI tool + one app-only tool, Streamable HTTP at exactly /mcp. Deleted in Prompt 8."""
import time
from pathlib import Path

import anyio
from mcp.server.apps import Apps, client_supports_apps
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult, TextContent, ToolAnnotations

UI = "ui://clarity-spike/hello.html"
apps = Apps()


@apps.tool(resource_uri=UI, description="Spike: says hello to `name`. Takes `seconds` (default 3) and reports progress once a second.",
           annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
async def spike_hello(name: str, ctx: Context, seconds: int = 3) -> CallToolResult:
    started = time.time()
    for i in range(seconds):  # (C) probe: do progress notifications reach the view? Also the timeout probe.
        await ctx.report_progress(i + 1, seconds, f"step {i + 1} of {seconds}")
        await anyio.sleep(1)
    text = f"hello, {name}"
    return CallToolResult(
        content=[TextContent(type="text", text=text if client_supports_apps(ctx) else f"{text} (text-only client)")],
        structured_content={"greeting": text, "name": name, "started": started, "finished": time.time()})


@apps.tool(resource_uri=UI, visibility=["app"], description="Spike: app-only ping (hidden from the model).")
def spike_ping() -> str:
    return f"pong at {time.strftime('%H:%M:%S')}"


apps.add_html_resource(UI, (Path(__file__).parent / "hello.html").read_text(), title="Clarity spike",
                       prefers_border=True)

mcp_server = MCPServer("clarity-spike", extensions=[apps])
# Default path "/mcp": main.py splices this route into FastAPI, so it answers at exactly /mcp (a Mount would 307).
# Stateless: no Mcp-Session-Id to carry, so browser hosts work without CORS expose-headers and any instance can answer.
mcp_app = mcp_server.streamable_http_app(stateless_http=True)
