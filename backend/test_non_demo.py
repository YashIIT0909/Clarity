"""Regression: a real (non-demo) product has none of the hand-written demo fields (category, market_query, match,
brand->ticker). Missing must mean unknown / unavailable: never an exception, never a penalty.

Bug it guards: a pasted Amazon dumbbell link died in timing with KeyError: 'category' (2026-09-29).

Runs from a recorded fixture, not serpapi_cache (whose rows expire): 0 SerpApi searches, SerpApi blocked, narrator
forced onto its rule-based fallback. Writes to Supabase like test_gate.py.
  .venv/bin/python test_non_demo.py            # run
  .venv/bin/python test_non_demo.py --record   # re-record the fixture from serpapi_cache (fails if anything is missing)
"""
import gzip
import json
import sys
from pathlib import Path

import narrator
import orchestrator
import receipts
import serpapi
import trust
from events import EVENTS
from resolve import resolve

FIXTURE = Path(__file__).parent / "fixtures" / "non_demo.json.gz"
DUMBBELL = "https://www.amazon.com/dp/B01LR5S6HK"  # Amazon Basics Neoprene Dumbbell Hand Weights
EBAY_ITEM = "178516729136"  # a real eBay listing; its Buy It Now price is removed below to mimic an auction


def _block_serpapi():
    serpapi.httpx.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("SerpApi blocked in this test"))


def cases():
    _, dumbbell = resolve(DUMBBELL)
    assert "category" not in dumbbell and not dumbbell.get("exclude"), dumbbell  # a genuine non-demo product
    bare = {k: dumbbell[k] for k in ("marketplace", "listing_id", "trend_query", "label")}  # no market_query/match/brand
    return dumbbell, bare


def record():
    _block_serpapi()
    seen, real = {}, serpapi.cached

    def rec(engine, cache_key=None, **params):
        r = real(engine, cache_key, **params)
        if r is not None:
            seen[f"{engine}:{serpapi._hash(engine, cache_key or params)}"] = r
        return r

    serpapi.cached = rec
    dumbbell, bare = cases()
    for p in (dumbbell, bare):
        t = trust.run(p)
        if t["verdict"] == "pass":
            __import__("timing").run(p, trust_signals=t["signals"])
    trust._listing_ebay(EBAY_ITEM)
    FIXTURE.parent.mkdir(exist_ok=True)
    FIXTURE.write_bytes(gzip.compress(json.dumps(seen, default=str).encode()))
    print(f"recorded {len(seen)} responses -> {FIXTURE} ({FIXTURE.stat().st_size:,} bytes)")


def run():
    fixture = json.loads(gzip.decompress(FIXTURE.read_bytes()))
    # An eBay listing with no single Buy It Now price (auction-only / multi-option).
    key = f"ebay_product:{serpapi._hash('ebay_product', {'product_id': EBAY_ITEM})}"
    fixture[key]["product_results"].pop("buy", None)

    served = lambda engine, cache_key=None, **params: fixture.get(f"{engine}:{serpapi._hash(engine, cache_key or params)}")
    serpapi.cached = served
    _block_serpapi()
    narrator.httpx.post = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("no LLM in tests"))
    receipts.httpx.post = narrator.httpx.post

    dumbbell, bare = cases()

    # 1. The pasted link as resolved: no category, a brand with no ticker. The original crash.
    r = orchestrator.run(dumbbell, label="test-non-demo:dumbbell")
    assert r["trust"]["verdict"] in ("pass", "fail") and r["final_verdict"] in ("buy_now", "wait", "walk_away")
    if r["timing"]:
        comp = r["timing"]["signals"]["components"]
        assert comp["maker"]["ticker"] is None and comp["maker"]["score"] == 0  # no ticker -> neutral
        ev = comp["demand"]["event"]  # unknown category: only all-category events can appear (which one is date-dependent)
        assert ev is None or EVENTS[ev["name"]]["categories"] == "all", ev
    assert narrator._facts(r) and r["narration"]["steps"]  # facts build; fallback narration rendered
    if r["timing"]:  # no model identifier in the name -> lifecycle unavailable, no searches, no exception
        lc = r["timing"]["signals"]["components"]["lifecycle"]
        assert (lc["status"], lc["score"], lc["why"]) == ("unavailable", 0, "no model identifier in the product name"), lc
    # Receipts: no parsable model -> "unavailable", never an exception (None when trust failed: not run).
    assert r["receipts"] is None or (r["receipts"]["status"], r["receipts"]["why"]) == \
        ("unavailable", "no model to match review videos and forums on"), r["receipts"]
    print(f"1. dumbbell (no category, no ticker): {r['final_verdict']} — trust {r['trust']['signals']['score']}, "
          f"receipts {r['receipts'] and r['receipts']['status']}")

    # 2. No market_query / match / brand at all: price check skipped, never a market_unknown penalty.
    r = orchestrator.run(bare, label="test-non-demo:bare")
    checks = [p["check"] for p in r["trust"]["signals"]["penalties"]]
    assert "market_unknown" not in checks, checks
    assert any("price check was skipped" in p for p in r["trust"]["signals"]["positives"])
    print(f"2. no market_query/match/brand: {r['final_verdict']} — penalties {checks or 'none'}")

    # 3. eBay listing with no single price: no KeyError, no market searches, not flagged for it.
    t = trust.run({"marketplace": "ebay", "listing_id": EBAY_ITEM, "market_query": "AirPods Pro 2", "match": ["airpods pro"]})
    checks = [p["check"] for p in t["signals"]["penalties"]]
    assert t["signals"]["listing"]["price"] is None and not {"market_unknown", "too_cheap", "too_expensive"} & set(checks)
    assert any("no single fixed price" in p for p in t["signals"]["positives"])
    print(f"3. eBay listing with no single price: trust {t['verdict']} — penalties {checks or 'none'}")
    # 4. Google Trends has nothing for the term (Motorola Sound MA1, 2026-09-30: timing died on KeyError
    #    'interest_over_time'): demand not judged, never an exception.
    niche = {**bare, "trend_query": "Motorola Sound MA1"}
    fixture[f"google_trends:{serpapi._hash('google_trends', {'q': niche['trend_query'], 'data_type': 'TIMESERIES', 'date': 'today 5-y'})}"] = \
        {"no_results": "Google Trends hasn't returned any results for this query."}
    import timing
    bare_signals = trust.run(bare)["signals"]
    tm = timing.run(niche, trust_signals=bare_signals)
    assert tm["signals"]["components"]["demand"]["score"] == 0 and any("demand wasn't judged" in x for x in tm["reasons"])
    # ...and with no price and no ticker either, nothing was judged: buy, no confidence figure, never a 0.0 -> WAIT.
    assert (tm["verdict"], tm["confidence"], tm["judged"]) == ("buy", None, False), tm
    assert timing.NOT_JUDGED in tm["reasons"] and not any(c["judged"] for c in tm["signals"]["components"].values())
    # "Motorola Sound MA1" has a model identifier, so lifecycle tries its 3 engines; SerpApi is blocked here, so every
    # engine errors -> unavailable, never an exception or a penalty. signals_json must stay JSON-serialisable.
    lc = tm["signals"]["components"]["lifecycle"]
    assert (lc["status"], lc["score"]) == ("unavailable", 0) and set(lc["engines_failed"]) == {"trends", "autocomplete", "news"}
    __import__("json").dumps(tm["signals"])
    # End to end through the orchestrator: the timing_results row is saved with confidence null (needs the
    # 2026-09-30 migration: timing_results.confidence nullable), the final verdict is buy_now with no figure.
    r = orchestrator.run(niche, label="test-non-demo:not-judged")
    row = serpapi.db.table("timing_results").select("verdict, confidence, signals_json").eq("query_id", r["query_id"]).execute().data
    assert row and (row[0]["verdict"], row[0]["confidence"], row[0]["signals_json"]["judged"]) == ("buy", None, False), row
    assert (r["final_verdict"], r["confidence"]) == ("buy_now", None) and r["narration"]["headline"] == narrator.NOT_JUDGED_HEADLINE
    print(f"4. no Trends / price / ticker: timing {tm['verdict']}, confidence {tm['confidence']} — not judged; "
          f"saved timing_results row: verdict {row[0]['verdict']}, confidence {row[0]['confidence']}")

    # 5. A real tie (a component judged, total exactly 0) stays as today: wait at 50%.
    real_maker = timing._maker
    timing._maker = lambda brand: (0.0, [("XYZ stock flat over 6 months", "neutral")], {"ticker": "XYZ", "six_month_change": 0.0})
    tm = timing.run(niche, trust_signals=bare_signals)
    timing._maker = real_maker
    assert (tm["verdict"], tm["confidence"], tm["judged"], tm["signals"]["score"]) == ("wait", 0.5, True, 0.0), tm
    assert timing.NOT_JUDGED not in tm["reasons"]
    print(f"5. real tie (maker judged, score 0.0): timing {tm['verdict']} {tm['confidence']:.0%}")
    # 6. Receipts for a product with a model but no usable videos: "unavailable", never an exception or a penalty.
    ma1 = {**bare, "brand": "Motorola", "label": "Motorola Sound MA1"}
    listing = {"title": "Motorola Sound MA1, the Only Google Authorized Wireless Android Auto Car Adapter"}
    rx = receipts.run(ma1, listing, live=True)  # SerpApi blocked: every engine errors
    assert rx["status"] == "unavailable" and rx["engines"]["youtube"].startswith("unavailable"), rx
    assert receipts.run(ma1, listing)["status"] == "on_request"  # cache-only: nothing cached -> offered on request
    yt = {"search_query": "Motorola Sound MA1 review", "gl": "us", "hl": "en"}
    fixture[f"youtube:{serpapi._hash('youtube', yt)}"] = {"video_results": [  # only unrelated videos come back
        {"video_id": "x1", "title": "Best Android Auto adapters 2026", "channel": {"name": "A"}, "length": "9:00", "views": 9},
        {"video_id": "x2", "title": "Motorola Sound MA1 unboxing", "channel": {"name": "B"}, "length": "6:00", "views": 5}]}
    rx = receipts.run(ma1, listing, live=True)
    assert rx["status"] == "unavailable" and rx["videos"]["status"] == "unavailable" and not rx["videos"]["picked"], rx
    assert {v["why"] for v in rx["videos"]["rejected"]} == {"doesn't name Motorola", "unboxing / first look"}, rx
    assert receipts.run({**bare, "brand": None}, listing, live=True)["status"] == "unavailable"  # no brand at all
    print(f"6. receipts, model but no usable videos: {rx['status']} ({rx['why']})")
    print("NON-DEMO REGRESSION PASSED (0 SerpApi searches)")


if __name__ == "__main__":
    record() if "--record" in sys.argv else run()
