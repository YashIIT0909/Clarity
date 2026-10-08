"""Demo replay proof: every chip, end to end through the orchestrator (trust, timing, narration, owner receipts and
"What owners say"), with the cache TTL forced to 0 (only pinned rows count), SerpApi blocked and Groq blocked.
A pinned demo costs 0 searches and 0 Groq tokens: any row that isn't pinned shows up here as a fallback or an
"on request" instead of being fetched. Also checks the SerpApi Account API before and after.
  .venv/bin/python test_demo_replay.py
"""
import os
from datetime import timedelta
from pathlib import Path

import httpx

import serpapi
import orchestrator
from demo_products import DEMO_CHIPS, DEMO_SCREENSHOTS
from resolve import resolve_screenshot

left = lambda: httpx.get(f"https://serpapi.com/account.json?api_key={os.environ['SERPAPI_KEY']}", timeout=30).json()["plan_searches_left"]
before = left()
real_post, real_get = httpx.post, httpx.get
serpapi.TTL = timedelta(0)
serpapi.httpx.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("SerpApi blocked: pinned rows only"))
httpx.post = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("Groq blocked: pinned narration only"))

runs = list(DEMO_CHIPS.items()) + [(k, resolve_screenshot((Path(__file__).parent / s["file"]).read_bytes(), s["price"])[1])
                                   for k, s in DEMO_SCREENSHOTS.items()]
expect = {"airpods-ebay": ("walk_away", None), "airpods-openbox": ("walk_away", None), "sony-xm5": ("buy_now", "found"),
          "honeywell-heater": ("buy_now", "unavailable"), "switch2": ("buy_now", "found"), "screenshot-sony-59": ("walk_away", None),
          "honeywell-ebay": ("wait", "unavailable")}
for key, product in runs:
    events = list(orchestrator.stream(product, label=f"replay-proof:{key}"))
    r = events[-1]["result"]
    owners = next((e for e in events if e.get("stage") == "owners"), None)
    rx = r["receipts"]
    assert not r["narration"].get("fallback"), (key, "verdict narration not pinned", r["narration"].get("error"))
    assert (r["final_verdict"], rx and rx["status"]) == expect[key], (key, r["final_verdict"], rx and rx["status"])
    # Where to buy instead, on the verdict event the UI reads (the eBay walk-aways are used: no store list, so none).
    verdict_event = next(e for e in events if e.get("stage") == "verdict")
    alt = verdict_event["alternative"]
    assert (alt and alt["store"]) == {"screenshot-sony-59": "Best Buy", "honeywell-ebay": "Walmart Supercenter"}.get(key), (key, alt)
    plan = verdict_event["wait_plan"]  # wait only: the eBay heater, 83% over market
    assert (plan and (plan["target"]["price"], plan["window"]["kind"])) == ({"honeywell-ebay": (36.24, "event")}.get(key)), (key, plan)
    if rx and rx["status"] == "found":
        assert not owners["fallback"], (key, "owners narration not pinned", owners.get("error"))
    types = [e.get("type") for e in events if e.get("stage") == "receipts"]
    print(f"{key:20} {r['final_verdict']:9} {r['confidence']} | receipts {rx and rx['status']} | events {types} | "
          f"narration pinned, owners {'pinned' if owners and not owners['fallback'] else owners and 'fixed line'}")
httpx.post, httpx.get = real_post, real_get
after = left()
assert before == after, (before, after)
print(f"DEMO REPLAY PASSED: {len(runs)} chips, TTL 0, SerpApi + Groq blocked; Account API {before} -> {after} searches left")
