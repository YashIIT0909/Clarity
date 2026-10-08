"""Where to buy instead, on pinned demo data: the cache TTL forced to 0 (pinned rows only) and SerpApi blocked, so it
costs 0 searches. Trust only (no orchestrator): writes no queries/verdict rows. The rules themselves are self-checked in
alternatives.py (synthetic stores); this proves them on the real store lists.
  .venv/bin/python test_alternatives.py
"""
from datetime import timedelta
from pathlib import Path

import serpapi

serpapi.TTL = timedelta(0)
serpapi.httpx.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("SerpApi blocked: pinned rows only"))

import alternatives  # noqa: E402
import trust  # noqa: E402
from demo_products import DEMO_CHIPS, DEMO_PRODUCTS, DEMO_SCREENSHOTS  # noqa: E402
from resolve import resolve_screenshot  # noqa: E402

assert alternatives.Z_BAND == trust.Z_TOO_CHEAP == trust.Z_TOO_EXPENSIVE

# The retailer itself, never a marketplace seller on its platform (names as Google Shopping gives them).
for name, major in [("Best Buy", True), ("Walmart", True), ("Walmart Supercenter", True), ("BJ's Wholesale Club", True),
                    ("Sony", True), ("Walmart - The Busy Bungalow", False), ("Newegg.com - Teds Electronics", False),
                    ("BrandsMart USA", False), ("Sylvane.com", False), ("eBay", False), ("Targeted Deals", False), (None, False)]:
    assert trust._is_major(name) == major, (name, major)

runs = list(DEMO_CHIPS.items()) + [
    (k, resolve_screenshot((Path(__file__).parent / s["file"]).read_bytes(), s["price"])[1]) for k, s in DEMO_SCREENSHOTS.items()]
# Expected offer on the demo's own verdict. The two eBay walk-aways are used listings: their band is other used eBay
# listings, so there is no retailer store list and no alternative (missing data, never guessed).
VERDICT = {"airpods-ebay": "walk_away", "airpods-openbox": "walk_away", "sony-xm5": "buy_now",
           "honeywell-heater": "buy_now", "switch2": "buy_now", "screenshot-sony-59": "walk_away", "honeywell-ebay": "wait"}
EXPECT = {"screenshot-sony-59": ("Best Buy", 254.99), "honeywell-ebay": ("Walmart Supercenter", 36.24)}
for key, product in runs:
    s = trust.run(product)["signals"]
    listing, market = s["listing"], s["market"]
    for st in market["stores"]:
        assert {"store", "price", "link", "major"} <= set(st), (key, st)
    alt = alternatives.offer(VERDICT[key], listing, market, product.get("store_domain"))
    got = alt and (alt["store"], alt["price"])
    assert got == EXPECT.get(key), (key, got)
    if alt:  # a real link, a fair price (inside the band), never the listing's own store
        assert alt["link"].startswith("https://") and abs(alt["price"] - market["median"]) <= 2 * market["sigma"], alt
    # Honeywell: Walmart is cheaper than the Amazon listing, so a WAIT would show it. Buy now never does.
    if key == "honeywell-heater":
        w = alternatives.offer("wait", listing, market)
        assert (w["store"], w["saving"]) == ("Walmart Supercenter", 5.75), w
    print(f"{key:20} {VERDICT[key]:9} {len(market['stores'])} stores -> {alt and alt['text']}")
print("ALTERNATIVES TEST PASSED (pinned data, 0 searches)")
