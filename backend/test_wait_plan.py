"""Wait plan (timing.wait_plan): what a WAIT waits for (a target price) and when to check again (a window), with
synthetic inputs, plus a WAIT forced through timing.run's real verdict logic. 0 SerpApi searches (blocked).
  .venv/bin/python test_wait_plan.py
"""
import random
from datetime import date, timedelta

import serpapi

serpapi.httpx.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("SerpApi blocked: 0 searches"))

import timing  # noqa: E402
import trust  # noqa: E402

TODAY = date(2026, 10, 9)  # next all-category event: Black Friday (2026-11-27), 49 days
QUIET = date(2026, 12, 10)  # no event within 84 days (Prime Day 2027 has no date yet)


def market(median=300.0, sigma=20.0, *stores, basis="new"):
    return {"median": median, "sigma": sigma, "basis": basis, "stores_observed_at": None,
            "stores": [{"store": n, "price": p, "link": f"https://{n.lower().replace(' ', '')}.example/p", "major": True}
                       for n, p in stores]}


def parts(lifecycle=None, seasonal=None):
    return {"lifecycle": (0.0, [], lifecycle or {"status": "none"}),
            "demand": (0.0, [], {} if seasonal is None else {"seasonal_ratio": seasonal})}


def announced(days=None, window=None, passed=False, today=TODAY):
    d = days is not None and (today + timedelta(days)).isoformat() or None
    rel = {"status": "announced", "release_date": d, "release_window": window or d, "date_passed": passed}
    return {"status": "announced", "successor": "Sony WH-1000XM7",
            "candidates": [{"name": "Sony WH-1000XM6", "release": {**rel, "release_date": None}},  # not the top one
                           {"name": "Sony WH-1000XM7", "release": rel}]}


listing = lambda price, mp="amazon": {"marketplace": mp, "price": price}
plan = lambda l, m, p=None, today=TODAY, category=None: timing.wait_plan({"category": category}, l, m, p or parts(), today)

# --- Target price -------------------------------------------------------------------------------------------------
p = plan(listing(330.0), market())
assert p["target"] == {"price": 280.0, "basis": "band", "price_text": "$280.00",
                       "reason": "a good price: one typical price spread ($20.00) below the new-retail median of $300.00"}, p
assert p["text"].startswith("Wait for $280.00 or less (a good price"), p["text"]
# A trusted store cheaper than the listing is the target: a price someone charges today (the cheapest such store).
p = plan(listing(330.0), market(300.0, 20.0, ("Best Buy", 275.0), ("Target", 290.0)))
assert (p["target"]["price"], p["target"]["basis"]) == (275.0, "store") and "Best Buy sells it for $275.00 today" in p["text"], p
# ...even when it's above the good price (median - sigma, $280): no hoped-for $280 while Target charges $290 now.
assert plan(listing(330.0), market(300.0, 20.0, ("Target", 290.0)))["target"] == {
    "price": 290.0, "basis": "store", "price_text": "$290.00", "reason": "Target sells it for $290.00 today"}
# A store at or above the listing's price isn't a target: the band target applies instead.
assert plan(listing(330.0), market(300.0, 20.0, ("Target", 330.0)))["target"]["basis"] == "band"
# The listing's own store never sets the target (Batch 1 rule).
assert plan(listing(330.0), market(300.0, 20.0, ("Amazon.com", 270.0)))["target"]["basis"] == "band"
# Whole dollars for a computed target.
assert plan(listing(400.0), market(333.33, 21.5))["target"]["price"] == 311.0
# Used listings say which band they're measured against.
assert "the usual price for used units of $65.00" in plan(listing(80.0, "ebay"), market(65.0, 16.25, basis="used"))["text"]
# Listing already at or under the target: no target (the wait is about something else). No price / no band: no target.
assert plan(listing(279.0), market())["target"] is None
assert plan(listing(None), market())["target"] is None
assert plan(listing(330.0), market(None, None))["target"] is None
# Property: the target is never below the too_cheap line (median - 2 sigma), never at or above the listing price.
rng = random.Random(9)
for _ in range(2000):
    med = rng.uniform(5, 2000)
    sig = rng.uniform(trust.MIN_SPREAD, trust.MAX_SPREAD) * med
    stores = [(f"S{i}", rng.uniform(med - 2 * sig, med + 2 * sig)) for i in range(rng.randint(0, 4))]
    price = rng.uniform(med * 0.6, med * 1.6)
    t = plan(listing(price), market(med, sig, *stores), today=QUIET)
    if t and t["target"]:
        assert med - trust.Z_TOO_CHEAP * sig <= t["target"]["price"] < price, (med, sig, price, t)

# --- Window: successor, then retail event, then season, then none --------------------------------------------------
w = plan(listing(330.0), market(), parts(announced(20, "Oct 29")))["window"]
assert (w["kind"], w["days"]) == ("successor", 20) and "Sony WH-1000XM7 is out (due Oct 29, in 20 days)" in w["text"], w
w = plan(listing(330.0), market(), parts(announced(None, "Q4 2026")))["window"]  # a window with no single date
assert (w["kind"], w["date"], w["days"]) == ("successor", None, None) and "(due Q4 2026)" in w["text"], w
w = plan(listing(330.0), market(), parts(announced(-5, passed=True)))["window"]  # passed date: falls through
assert (w["kind"], w["days"]) == ("event", 49) and w["text"].startswith("Black Friday starts in 49 days"), w
assert "can't predict sale prices" in w["text"]  # a check-again time, never a price forecast
assert plan(listing(330.0), market(), today=date(2026, 10, 9), category="audio")["window"]["text"].startswith("Diwali")
w = plan(listing(330.0), market(), parts(seasonal=0.8), today=QUIET)["window"]
assert (w["kind"], w["days"]) == ("season", 56), w
assert plan(listing(330.0), market(), parts(seasonal=1.2), today=QUIET)["window"] is None  # interest rising: no window
assert plan(listing(330.0), market(), parts(seasonal=0.97), today=QUIET)["window"] is None  # inside the 5% dead zone
# Nothing to say at all -> no plan.
assert plan(listing(279.0), market(), today=QUIET) is None

# --- Through timing.run: only a WAIT gets a plan; it's saved in signals; it changes no score ------------------------
real = timing._trends, timing._maker, timing._price, timing._lifecycle
timing._trends = lambda q, market=None: []
timing._maker = lambda brand: (0.0, [], {"ticker": "X", "six_month_change": 0.0})
timing._lifecycle = lambda product, today, emit=None: (0.0, [], {"status": "none"})
sig = {"listing": {"marketplace": "amazon", "price": 330.0}, "market": market()}
for price_score, verdict in ((-0.5, "wait"), (0.5, "buy")):
    timing._price = lambda l, m, s=price_score: (s, [], {"position_vs_median": 0.1})
    r = timing.run({"trend_query": None, "category": "audio"}, sig)
    assert r["verdict"] == verdict and r["signals"]["score"] == round(0.45 * price_score, 3), r
    assert (r["wait_plan"] is not None) == (verdict == "wait") and r["signals"]["wait_plan"] == r["wait_plan"], r
    if verdict == "wait":
        assert r["wait_plan"]["target"]["price"] == 280.0 and r["wait_plan"]["window"], r["wait_plan"]
        print(f"forced WAIT -> {r['wait_plan']['text']}")
timing._trends, timing._maker, timing._price, timing._lifecycle = real
print("WAIT PLAN TEST PASSED (0 searches)")
