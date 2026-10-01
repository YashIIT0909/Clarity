"""Phase 0 probe: hit one SerpApi engine for one product, print raw JSON.

Usage:
  python phase0/probe.py amazon_search "sony wh-1000xm5"     # find ASINs
  python phase0/probe.py ebay_search "airpods pro 2"         # find eBay item IDs
  python phase0/probe.py amazon B09XS7JWHH                   # Amazon Product API
  python phase0/probe.py ebay 123456789012                   # eBay Product API
  python phase0/probe.py shopping "sony wh-1000xm5"          # Google Shopping
  python phase0/probe.py immersive <immersive_product_page_token>
  python phase0/probe.py trends "sony wh-1000xm5"            # 12-month interest
  python phase0/probe.py finance SONY:NYSE                   # Google Finance
  python phase0/probe.py finance-brand Sony                  # via tickers.py

Responses are cached in phase0/raw/ -- delete a file to force a re-fetch.
"""
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from tickers import BRAND_TICKERS

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"

ENGINES = {
    "amazon_search": lambda a: {"engine": "amazon", "k": a},
    "ebay_search": lambda a: {"engine": "ebay", "_nkw": a},
    "amazon": lambda a: {"engine": "amazon_product", "asin": a},
    "ebay": lambda a: {"engine": "ebay_product", "product_id": a},
    "shopping": lambda a: {"engine": "google_shopping", "q": a, "gl": "us", "hl": "en"},
    "immersive": lambda a: {"engine": "google_immersive_product", "page_token": a},
    "trends": lambda a: {"engine": "google_trends", "q": a, "data_type": "TIMESERIES", "date": "today 12-m"},
    "finance": lambda a: {"engine": "google_finance", "q": a},
    "finance-brand": lambda a: {"engine": "google_finance", "q": BRAND_TICKERS[a]},
}


def api_key():
    for line in (ROOT.parent / "backend" / ".env").read_text().splitlines():
        if line.startswith("SERPAPI_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("SERPAPI_KEY missing from Clarity/backend/.env")


def fetch(params):
    RAW.mkdir(exist_ok=True)
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]
    path = RAW / f"{params['engine']}_{key}.json"
    if path.exists():
        return json.loads(path.read_text()), path, True
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode({**params, "api_key": api_key()})
    try:
        data = json.load(urllib.request.urlopen(url, timeout=60))
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:500]}")
    path.write_text(json.dumps(data, indent=2))
    return data, path, False


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ENGINES:
        sys.exit(__doc__)
    data, path, cached = fetch(ENGINES[sys.argv[1]](sys.argv[2]))
    data.pop("search_metadata", None)
    print(json.dumps(data, indent=2))
    print(f"\n# {'cached' if cached else 'fetched'}: {path}", file=sys.stderr)
