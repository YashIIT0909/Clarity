"""India probe (Batch IN, 2026-10-09): what SerpApi returns for an Indian shopper, before building on it.
Six searches, all through serpapi.search (cached, so the India build can reuse them), raw responses saved to out/.
  1. amazon        k=<product>, amazon_domain=amazon.in       -> an amazon.in ASIN
  2. amazon_product asin=<it>, amazon_domain=amazon.in        -> price in ₹? seller / ships-from? condition?
  3. google_shopping q=<product>, gl=in, hl=en                -> which Indian stores, prices in ₹
  4. google_immersive_product  (first result with a token)    -> store list + links
  5. google_trends  TIMESERIES, 5 years, geo=IN               -> Indian demand
  6. google_finance VOLTAS:NSE, 6M                            -> NSE tickers work
Run from backend/: ../<venv>/python ../phase0/india_probe/probe.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
import serpapi  # noqa: E402

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
PRODUCT = "Sony WH-1000XM5"


def call(name, engine, **params):
    d = serpapi.search(engine, **params)
    (OUT / f"{name}.json").write_text(json.dumps(d, indent=1, ensure_ascii=False))
    print(f"\n=== {name}: {engine} {params}")
    return d


# 1-2. Listing on amazon.in
s = call("1_amazon_search", "amazon", k=PRODUCT, amazon_domain="amazon.in")
hits = [r for r in s.get("organic_results", []) if r.get("asin")]
for r in hits[:6]:
    print(f"  {r['asin']} | {r.get('price')!r} / {r.get('extracted_price')} | {r.get('title', '')[:70]}")
hit = next((r for r in hits if "1000xm5" in r.get("title", "").lower().replace("-", "")), hits[0] if hits else None)
if hit:
    d = call("2_amazon_product", "amazon_product", asin=hit["asin"], amazon_domain="amazon.in")
    p = d.get("product_results") or {}
    print(f"  title {p.get('title', '')[:80]}\n  price {p.get('price')!r} extracted {p.get('extracted_price')} | rating "
          f"{p.get('rating')} reviews {p.get('reviews')} | brand {p.get('brand')!r}")
    for k, offer in (d.get("purchase_options") or {}).items():
        feats = offer.get("features") or {}
        print(f"  purchase_options[{k}]: price {offer.get('extracted_price')} | features "
              f"{ {f: (v or {}).get('text') for f, v in feats.items()} }")
    print(f"  thumbnails: {len(p.get('thumbnails') or [])} | keys: {sorted(d)[:20]}")

# 3-4. Indian retail market
g = call("3_google_shopping", "google_shopping", q=PRODUCT, gl="in", hl="en")
rows = g.get("shopping_results", [])
print(f"  {len(rows)} results")
for r in rows[:20]:
    print(f"  {r.get('source', '')[:28]:28} | {r.get('price')!r:>14} {r.get('extracted_price')} | "
          f"{'token' if r.get('immersive_product_page_token') else '     '} | {r.get('title', '')[:55]}")
hero = next((r for r in rows if r.get("immersive_product_page_token") and "1000xm5" in
             r.get("title", "").lower().replace("-", "")), None)
if hero:
    imm = call("4_google_immersive_product", "google_immersive_product", page_token=hero["immersive_product_page_token"])
    stores = (imm.get("product_results") or {}).get("stores", [])
    print(f"  reference: {hero.get('title', '')[:60]} @ {hero.get('source')} | {len(stores)} stores")
    for st in stores:
        print(f"  {st.get('name', '')[:28]:28} | {st.get('price')!r:>14} {st.get('extracted_price')} | {(st.get('link') or 'NO LINK')[:60]}")

# 5. Indian demand
t = call("5_google_trends", "google_trends", q=PRODUCT, data_type="TIMESERIES", date="today 5-y", geo="IN")
tl = (t.get("interest_over_time") or {}).get("timeline_data") or []
print(f"  {len(tl)} weeks; last 12: {[x['values'][0]['extracted_value'] for x in tl[-12:]]}")

# 6. NSE ticker
f = call("6_google_finance", "google_finance", q="VOLTAS:NSE", window="6M")
gr = f.get("graph") or []
print(f"  {len(gr)} points; first {gr[0] if gr else None}; last {gr[-1] if gr else None}; "
      f"summary {(f.get('summary') or {}).get('title')!r} {(f.get('summary') or {}).get('currency')!r}")
