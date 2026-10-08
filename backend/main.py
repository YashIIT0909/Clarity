import io
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from PIL import Image
from pydantic import BaseModel

load_dotenv()

import locales  # noqa: E402
import orchestrator  # noqa: E402
from demo_products import DEMO_CHIPS  # noqa: E402
from resolve import ResolveError, resolve, resolve_screenshot  # noqa: E402

from clarity_mcp.server import mcp_app, mcp_server  # noqa: E402


@asynccontextmanager
async def lifespan(app):
    async with mcp_server.session_manager.run():  # the SDK's Streamable HTTP sessions need this running
        yield


app = FastAPI(title="Clarity", lifespan=lifespan)
app.router.routes.extend(mcp_app.routes)  # POST/GET/DELETE /mcp exactly: a route, not a Mount, so no /mcp/ 307
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


# Which market typed text and screenshots are checked in. A link decides by itself (amazon.in -> India). The web app
# is for the SerpApi India hackathon: India by default.
Market = Literal["in", "us"]
DEFAULT_MARKET: Market = "in"
MAX_AD_PRICE = {"us": 100_000, "in": 10_000_000}  # $1 lakh is a lot of headphones; ₹1 lakh is a phone


class AnalyzeRequest(BaseModel):
    q: str  # product URL, free text, or demo key
    market: Market = DEFAULT_MARKET


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/demo-products")
def demo_products():
    return [{"key": k, "label": p["label"], "url": p["url"], "market": p.get("market", "us")} for k, p in DEMO_CHIPS.items()]


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    try:
        label, product = resolve(req.q, req.market)
    except ResolveError as e:
        raise HTTPException(422, str(e))
    return orchestrator.run(product, label=label)


def _sse(event):
    return f"data: {json.dumps(event, default=str)}\n\n"


def _stream(message, do_resolve, extra=lambda product: {}):
    """SSE: one `data: {json}` message per pipeline step as it happens. Same events for links and screenshots."""

    def events():
        try:
            yield _sse({"stage": "resolve", "status": "running", "message": message})
            label, product = do_resolve()
            yield _sse({"stage": "resolve", "status": "done", "label": product.get("label", label),
                        "marketplace": product["marketplace"], "listing_id": product["listing_id"],
                        "market": orchestrator._market_info(product), **extra(product)})
            for e in orchestrator.stream(product, label=label):
                yield _sse(e)
        except ResolveError as e:
            yield _sse({"stage": "error", "message": str(e)})
        except Exception as e:  # noqa: BLE001 — surface pipeline failures to the client instead of a dropped stream
            yield _sse({"stage": "error", "message": f"Something went wrong while analysing this product: {str(e)[:200]}"})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/analyze/stream")
def analyze_stream(q: str, market: Market = DEFAULT_MARKET):
    """market: for typed text only; a link decides its own market."""
    return _stream("Finding the listing…", lambda: resolve(q, market))


@app.get("/receipts/stream/{query_id}")
def receipts_stream(query_id: str):
    """"See what owners say": the live receipts run for a product whose receipts weren't cached (SSE)."""
    def events():
        try:
            for e in orchestrator.receipts_on_request(query_id):
                yield _sse(e)
        except Exception as e:  # noqa: BLE001
            yield _sse({"stage": "receipts", "type": "error", "message": f"receipts failed: {str(e)[:200]}"})
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # before compression; photo_forensics re-encodes under SerpApi's 500 KB
IMAGE_FORMATS = {"PNG", "JPEG", "WEBP"}
JOB_TTL = 600  # s an uploaded screenshot waits to be streamed
# ponytail: in-memory, single process. A multi-worker deploy needs a shared store (Supabase Storage / Redis).
JOBS: dict[str, dict] = {}


@app.post("/analyze/image")
async def analyze_image(file: UploadFile, price: float | None = Form(None), market: Market = Form(DEFAULT_MARKET)):
    """Screenshot upload -> job id for /analyze/image/stream/{id}. `price` = the price shown in the ad, typed by the
    user: Clarity never reads prices from the image. `market`: a screenshot has no country, so the user picks one."""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Image is larger than 10 MB.")
    try:  # the decoded format, not the client's Content-Type
        fmt = Image.open(io.BytesIO(data)).format
        Image.open(io.BytesIO(data)).verify()
    except Exception:
        raise HTTPException(415, "That file isn't a readable image. Upload a PNG, JPG or WebP screenshot.")
    if fmt not in IMAGE_FORMATS:
        raise HTTPException(415, f"{fmt} images aren't supported. Upload a PNG, JPG or WebP screenshot.")
    if price is not None and not 0 < price < MAX_AD_PRICE[market]:
        raise HTTPException(422, f"Price in the ad must be between {locales.money(0, market)} and "
                                 f"{locales.money(MAX_AD_PRICE[market], market)}.")
    now = time.time()
    for k in [k for k, j in JOBS.items() if now - j["at"] > JOB_TTL]:
        del JOBS[k]
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"data": data, "price": price, "market": market, "at": now}
    return {"job_id": job_id}


@app.get("/analyze/image/stream/{job_id}")
def analyze_image_stream(job_id: str):
    job = JOBS.pop(job_id, None)
    if not job:
        raise HTTPException(404, "Unknown or expired upload. Upload the screenshot again.")
    return _stream("Identifying the product in your screenshot…",
                   lambda: resolve_screenshot(job["data"], job["price"], job["market"]),
                   lambda p: {"identified_as": p["lens"]["identified_as"], "ad_price": p["ad_price"],
                              "store_domain": p["store_domain"], "store_note": p["lens"]["store_note"],
                              "price_note": "Prices are never read from the screenshot; only the price you typed is checked."})
