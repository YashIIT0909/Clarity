"""Successor Radar probe (Prompt 1): Trends rising related queries + Google Autocomplete + Google News per product,
Amazon Autocomplete for Sony only. Locale US for every call. Raw JSON -> out/<product>_<engine>.json.
Existing files are reused (re-runs cost 0 searches).
Run: ../../backend/.venv/bin/python probe.py   (from this dir)"""
import json
import os
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
BACKEND = HERE.parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)
from dotenv import load_dotenv  # noqa: E402
from demo_products import DEMO_PRODUCTS  # noqa: E402

load_dotenv(BACKEND / ".env")
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
KEY = os.environ["SERPAPI_KEY"]

# Product name as a shopper types it (autocomplete, news). Trends reuses each demo product's trend_query.
NAMES = {"sony-xm5": "sony wh-1000xm5", "switch2": "nintendo switch 2", "honeywell-heater": "honeywell 360 heater"}
AMAZON_FOR = "sony-xm5"  # the one product with an expected successor

# Param names from SerpApi docs (2026-09-30).
ENGINES = {
    "trends_rising": lambda k, n: {"engine": "google_trends", "q": DEMO_PRODUCTS[k]["trend_query"],
                                   "data_type": "RELATED_QUERIES", "date": "today 3-m", "geo": "US", "hl": "en"},
    "google_autocomplete": lambda k, n: {"engine": "google_autocomplete", "q": n, "gl": "us", "hl": "en"},
    # "successor" over "new model": tech press uses "successor" for the next generation; "new model" also matches
    # reviews, colourways and model-year refreshes of unrelated products.
    "google_news": lambda k, n: {"engine": "google_news", "q": f"{n} successor", "gl": "us", "hl": "en"},
    "amazon_autocomplete": lambda k, n: {"engine": "amazon_autocomplete", "k": n, "amazon_domain": "amazon.com"},
}


def call(params, path):
    if path.exists():
        return json.loads(path.read_text())
    t = time.monotonic()
    try:
        data = httpx.get("https://serpapi.com/search.json", params={**params, "api_key": KEY}, timeout=90).json()
    except Exception as e:  # keep going; record the failure
        data = {"error": repr(e)}
    data["_probe_latency_s"] = round(time.monotonic() - t, 2)
    path.write_text(json.dumps(data, indent=1))
    return data


if __name__ == "__main__":
    for k, name in NAMES.items():
        for eng, mk in ENGINES.items():
            if eng == "amazon_autocomplete" and k != AMAZON_FOR:
                continue
            d = call(mk(k, name), OUT / f"{k}_{eng}.json")
            print(f"{k:17} {eng:20} {d.get('error') or 'ok'}  {d['_probe_latency_s']}s")
