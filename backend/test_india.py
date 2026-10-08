"""India as a market (Batch IN), on pinned data and synthetic inputs: 0 SerpApi searches (blocked), 0 Groq calls.
  1. the link decides: amazon.in -> India, amazon.com / eBay -> US, other Amazon sites refused; typed text -> caller's
  2. US params are byte-for-byte what Clarity always sent (every US cache key, pin and narration stays valid)
  3. an India verdict speaks ₹ only: no "$" in trust, timing, narration, alternative or wait plan
  4. India's rules: Amazon-affiliated sellers aren't first-party; no eBay, so no secondhand band (not a penalty);
     converted foreign prices (paise) and import shops never enter the Indian band
  5. Indian sale events
  6. the India scam ad: a screenshot priced in ₹ walks away, ₹ kept in the narration, Amazon.in as the alternative
  .venv/bin/python test_india.py
"""
from datetime import date, timedelta
from pathlib import Path

import httpx
import serpapi

serpapi.TTL = timedelta(0)  # pinned rows only
serpapi.httpx.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("SerpApi blocked: pinned rows only"))
httpx.post = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("Groq blocked: pinned narration only"))

import events  # noqa: E402
import lifecycle  # noqa: E402
import locales  # noqa: E402
import orchestrator  # noqa: E402
import resolve  # noqa: E402
import store_reputation  # noqa: E402
import timing  # noqa: E402
import trust  # noqa: E402
from demo_products import DEMO_PRODUCTS, DEMO_SCREENSHOTS  # noqa: E402

# 1. The link decides.
assert resolve.resolve("https://www.amazon.in/Sony-WH-1000XM5/dp/B09XS7JWHH?th=1")[0] == "sony-xm5-in"
assert resolve.resolve("https://www.amazon.com/dp/B09XS7JWHH")[0] == "sony-xm5"           # same ASIN, US demo
assert resolve.resolve("https://www.amazon.com/dp/B09XS7JWHH", "in")[0] == "sony-xm5"     # a link beats the picker
assert resolve.resolve("https://www.ebay.com/itm/178516729136", "in")[0] == "airpods-ebay"  # eBay: always US
for bad in ("https://www.amazon.co.uk/dp/B09XS7JWHH", "https://www.amazon.de/dp/B09XS7JWHH"):
    try:
        resolve.resolve(bad)
        raise AssertionError(bad)
    except resolve.ResolveError as e:
        assert "amazon.com and amazon.in" in str(e), e
called = []
resolve.search = lambda engine, **params: called.append((engine, params)) or {"organic_results": []}
for market, want in (("in", {"k": "boat headphones", "amazon_domain": "amazon.in"}), ("us", {"k": "boat headphones"})):
    try:
        resolve.resolve("boat headphones", market)
    except resolve.ResolveError:
        pass
    assert called.pop() == ("amazon", want), (market, want)  # typed text: the caller's market; US params unchanged
resolve.search = serpapi.search

# 2. US params are exactly the old ones (cache keys); India adds its own.
assert lifecycle.queries("Sony WH-1000XM5") == {
    "trends": ("google_trends", {"q": "Sony WH-1000XM5", "data_type": "RELATED_QUERIES", "date": "today 3-m",
                                 "geo": "US", "hl": "en"}),
    "autocomplete": ("google_autocomplete", {"q": "Sony WH-1000XM5", "gl": "us", "hl": "en"}),
    "news": ("google_news", {"q": "Sony WH-1000XM5 successor", "gl": "us", "hl": "en"})}
assert lifecycle.queries("x", "in")["trends"][1]["geo"] == "IN" and lifecycle.queries("x", "in")["news"][1]["gl"] == "in"
us_store = store_reputation.queries("shop-x.com")
assert us_store["reviews"][1] == {"q": "shop-x.com reviews", "gl": "us", "hl": "en"}
assert us_store["ads"][1] == {"text": "shop-x.com", "region": "2840"}
assert store_reputation.queries("shop-x.com", "in")["ads"][1]["region"] == "2356"
seen = []
timing.search = lambda engine, **params: seen.append((engine, params)) or {}
timing._trends("q"), timing._trends("q", "in")
assert seen == [("google_trends", {"q": "q", "data_type": "TIMESERIES", "date": "today 5-y"}),  # US: no geo, as always
                ("google_trends", {"q": "q", "data_type": "TIMESERIES", "date": "today 5-y", "geo": "IN"})], seen
timing.search = serpapi.search
assert locales.money(298) == "$298.00"  # US text byte-identical: it feeds cached narrations

# 3. The India demo speaks ₹ only (pinned replay through the orchestrator, as the web app runs it).
events_ = list(orchestrator.stream(DEMO_PRODUCTS["sony-xm5-in"], label="test-india:sony-xm5-in"))
r = events_[-1]["result"]
verdict = next(e for e in events_ if e.get("stage") == "verdict")
assert verdict["market"] == {"code": "in", "name": "India", "currency": "INR", "symbol": "₹"}, verdict["market"]
assert r["trust"]["signals"]["listing"]["market"] == "in" and not r["narration"].get("fallback"), "narration not pinned"
texts = (r["trust"]["reasons"] + r["timing"]["reasons"] + [r["narration"]["headline"], r["narration"]["summary"]]
         + [s["text"] for s in r["narration"]["steps"]])
assert not [t for t in texts if "$" in t], [t for t in texts if "$" in t]
assert any("₹19,989" in t for t in texts) and any("₹23,664" in t for t in texts)
stores = {s["store"]: s["major"] for s in r["trust"]["signals"]["market"]["stores"]}
assert stores.get("Flipkart") and stores.get("Amazon.in"), stores  # Indian retailers count as major
# Synthetic: an India wait plan and alternative are in ₹ too.
band = {"median": 24317.0, "sigma": 5010.0, "basis": "new", "stores_observed_at": None,
        "stores": [{"store": "Flipkart", "price": 19989, "link": "https://www.flipkart.com/x", "major": True}]}
pricey = {"marketplace": "amazon", "market": "in", "price": 27999}
plan = timing.wait_plan({"category": "audio", "market": "in"}, pricey,
                        band, {"lifecycle": (0, [], {}), "demand": (0, [], {})}, date(2026, 10, 9))
assert plan["text"].startswith("Wait for ₹19,989 or less (Flipkart sells it for ₹19,989 today). Big Billion Days "
                               "starts today"), plan["text"]
import alternatives  # noqa: E402
alt = alternatives.offer("wait", pricey, band)
assert alt["text"] == "Flipkart sells this product for ₹19,989, ₹8,010 less than this listing." and alt["saving_text"] == "₹8,010"

# 4. India's rules.
assert "Clicktech Retail Private Ltd" not in locales.get("in")["first_party"]  # affiliates' prices are checked
assert r["trust"]["signals"]["listing"]["seller_is_platform"] is False
assert r["trust"]["signals"]["listing"]["ships_from"] == "Amazon"
used = {"marketplace": "amazon", "market": "in", "id": "X", "condition_bucket": "used"}
m = trust._secondhand_market(used, "q", [])  # would search eBay in the US; India has no eBay: no search at all
assert m["no_source"] and m["median"] is None and m["basis"] == "used", m
assert trust._seller_listings({**used, "marketplace": "ebay"}, "q", lambda t: True) == []
assert trust._is_major("Flipkart", "in") and trust._is_major("Reliance Digital", "in") and not trust._is_major("Flipkart")
assert not trust._is_major("Cashify", "in") and trust._peers("in").search("Cashify")  # refurbished: a peer, not retail
loc_in = locales.get("in")
assert not trust._local_price(24317.31, loc_in) and trust._local_price(19989, loc_in) and trust._local_price(19989.0, loc_in)
assert trust._local_price(254.99, locales.get("us"))  # US cents are normal
for importer in ("shoptheworld.in", "wafuu.com", "ubuy.co.in", "Desertcart.ae"):
    assert trust._peers("in").search(importer), importer
band_in = r["trust"]["signals"]["market"]
assert band_in["median"] == 23664 and max(band_in["prices"]) < 2 * 19989, band_in["prices"]  # no 2x import prices

# 5. Indian sale events (in India only), and the same-day wording.
assert events.next_event(date(2026, 10, 9), None, market="in") == ("Big Billion Days", 0)
assert events.next_event(date(2026, 9, 1), None, market="in") == ("Great Indian Festival", 37)  # 2026-10-08
assert events.next_event(date(2026, 10, 9), None) == ("Black Friday", 49)                    # US: no Indian events
assert events.next_event(date(2026, 10, 10), "heating", market="in") == ("Diwali", 29)        # Diwali: all categories
assert events.next_event(date(2026, 10, 10), "heating") == ("Black Friday", 48)               # US Diwali: audio/gaming
assert events.next_event(date(2026, 6, 20), None, market="in") == ("Prime Day India", 14)
assert events.next_event(date(2026, 6, 20), "audio") == ("Prime Day", 3)                     # US Prime Day: its own date
assert events.next_event(date(2026, 1, 1), None, market="in") == ("Great Republic Day Sale", 15)
assert events.next_event(date(2026, 12, 1), None, market="in") is None  # 2027 dates unknown: nothing, never a guess

# 6. The India scam ad (synthetic, "Rs. 4,999"): Lens names it, the clean model term prices it in India, it walks away.
shot = DEMO_SCREENSHOTS["screenshot-sony-rs4999"]
_, ad = resolve.resolve_screenshot((Path(__file__).parent / shot["file"]).read_bytes(), shot["price"], "in")
assert (ad["market"], ad["market_query"]) == ("in", "Sony WH-1000XM5"), ad["market_query"]  # not Lens's long US name
ev_ad = list(orchestrator.stream(ad, label="test-india:screenshot-sony-rs4999"))
r_ad = ev_ad[-1]["result"]
assert r_ad["final_verdict"] == "walk_away" and not r_ad["narration"].get("fallback")
said = [r_ad["narration"]["headline"], r_ad["narration"]["summary"]] + [x["text"] for x in r_ad["narration"]["steps"]]
assert not [t for t in said if "$" in t] and any("₹4,999" in t for t in said), said  # the rupee guard restored it
import re as _re  # noqa: E402
assert not [t for t in said if _re.search(r"(?<![₹\d,])4,999\b", t)], said  # no bare amount left
# The ₹ guard also fixes a wrong sign: the LLM wrote "¥25,082" on the India wait demo (seen in the browser).
import narrator  # noqa: E402
fixed = narrator._rupees({"headline": "", "summary": "", "steps": [{"title": "", "text": "median of ¥25,082 or ¥ ₹29,990"}]},
                         {"trust": {"signals": {"listing": {"market": "in"}}, "reasons": ["₹25,082 and ₹29,990"]},
                          "timing": None})["steps"][0]["text"]
assert fixed == "median of ₹25,082 or ₹29,990", fixed
alt_ad = next(e for e in ev_ad if e.get("stage") == "verdict")["alternative"]
assert (alt_ad["store"], alt_ad["price_text"]) == ("Amazon.in", "₹19,989"), alt_ad
print("INDIA TEST PASSED (pinned data, 0 searches, 0 Groq calls)")
