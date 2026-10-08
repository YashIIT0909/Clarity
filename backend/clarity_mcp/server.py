"""The Clarity MCP server: one model-visible tool, served by main.py at exactly /mcp (Streamable HTTP, stateless)."""
from pathlib import Path
from typing import Annotated, Literal

from mcp.server.apps import Apps, ResourceCsp
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from clarity_mcp import contract, runner

TOP_REASONS = 3  # narrator steps added to the text block under the summary
VIEW = "ui://clarity/evidence-board.html"
VIEW_HTML = Path(__file__).parent / "view" / "dist" / "index.html"  # built by `npm run build` in view/ (committed)
# Images the view shows: listing photos (Amazon, eBay), photo-match thumbnails (SerpApi, Google, Bing), review videos.
IMAGE_HOSTS = ["https://m.media-amazon.com", "https://i.ebayimg.com", "https://serpapi.com",
               "https://*.gstatic.com", "https://*.mm.bing.net", "https://i.ytimg.com"]
VIDEO_PLAYER = ["https://www.youtube-nocookie.com"]  # "What owners say" plays a review at the quoted second

DESCRIPTION = """\
Checks whether a specific Amazon or eBay listing is safe to buy, and whether now is a good time to buy it. Clarity \
runs a trust check first (price against the market for the same condition, seller record, returns, photo forensics); \
only a listing that passes goes on to a timing check (demand trend, price position, maker signals, newer models).

Call it before recommending or buying a specific listing. Pass the listing's URL when you have one (amazon.in/dp/…, \
amazon.com/dp/… or ebay.com/itm/…): that checks that exact listing, in that listing's market (amazon.in: Indian \
retailers, ₹; amazon.com and eBay: US retailers, $). A product description also works, but is less precise: Clarity \
then picks an Amazon listing itself, on amazon.in unless `market` is "us".

Verdicts:
- walk_away: the listing failed the trust check. Clarity advises against buying it; timing is not checked.
- buy_now: the listing passed the trust check and timing favours buying now (with a confidence).
- wait: the listing passed the trust check, but timing favours waiting (with a confidence).
- buy_timing_not_judged: the listing passed the trust check; there wasn't enough data to judge timing.
- could_not_check: the check didn't complete. This is not a pass: the listing is unverified, not safe.

The result's summary line gives the verdict, its main reason and the date of the data; reasons and evidence give the \
details. On walk_away, or wait when it's cheaper, `alternative` may name a major retailer selling the same product at \
a normal market price, with a link: a safer place to buy. It is null when no such store was found. On wait, \
`wait_plan` may give a target price and when to check again (never a price forecast)."""

apps = Apps()


@apps.tool(resource_uri=VIEW, name="clarity_check_listing", title="Clarity: check a listing", description=DESCRIPTION,
            annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
def clarity_check_listing(
    listing: Annotated[str, Field(description="An Amazon or eBay product URL, or a product description.")],
    market: Annotated[Literal["in", "us"], Field(description="For a product description only: check it on amazon.in "
                                                 "against Indian retailers (in) or on amazon.com (us). A URL decides "
                                                 "its own market.")] = "in",
) -> CallToolResult:
    # Sync on purpose: the SDK runs sync tools in a worker thread, so a 45 s check never blocks the event loop.
    structured, meta = contract.build(runner.run(listing, market=market))
    text = structured["summary"]
    if structured["reasons"]:
        text += "\n" + "\n".join(f"- {r}" for r in structured["reasons"][:TOP_REASONS])
    if structured["wait_plan"]:
        text += f"\nWhat to wait for: {structured['wait_plan']['text']}"
    if structured["alternative"]:
        text += f"\nWhere to buy instead: {structured['alternative']['text']} {structured['alternative']['link']}"
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=structured, meta=meta)


@apps.tool(resource_uri=VIEW, visibility=["app"], name="clarity_owner_receipts",
           description="For the Clarity view only: 'What owners say' (reviews and forums) for a finished check.",
           annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
def clarity_owner_receipts(query_id: str, live: bool = False) -> CallToolResult:
    # App-only (hidden from the model). Context only: never changes the verdict. See runner.receipts for live=True.
    r = runner.receipts(query_id, live)
    return CallToolResult(content=[TextContent(type="text", text=f"What owners say: {r['status']}")], structured_content=r)


apps.add_html_resource(VIEW, VIEW_HTML.read_text(), title="Clarity Evidence Board", prefers_border=True,
                       csp=ResourceCsp(resource_domains=IMAGE_HOSTS, frame_domains=VIDEO_PLAYER))
mcp_server = MCPServer("clarity", extensions=[apps])

# Default path "/mcp": main.py splices this route into FastAPI, so it answers at exactly /mcp (a Mount would 307).
# Stateless: no Mcp-Session-Id to carry, so browser hosts work without CORS expose-headers and any instance can answer.
mcp_app = mcp_server.streamable_http_app(stateless_http=True)
