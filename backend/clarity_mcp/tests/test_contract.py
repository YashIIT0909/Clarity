"""Runner + contract on the 5 text chips (by their URLs, as an agent would pass them) and the could_not_check paths.
TTL 0, SerpApi and Groq blocked like test_demo_replay.py: pinned rows only, 0 searches (Account API before/after).
Writes the same Supabase rows a web run does.
  .venv/bin/python -m clarity_mcp.tests.test_contract            (from backend/)
"""
import json
import os
import time
from datetime import timedelta

import httpx
from dotenv import load_dotenv

load_dotenv()

import orchestrator  # noqa: E402
import serpapi  # noqa: E402
from clarity_mcp import contract, runner  # noqa: E402
from demo_products import DEMO_CHIPS  # noqa: E402

left = lambda: httpx.get(f"https://serpapi.com/account.json?api_key={os.environ['SERPAPI_KEY']}", timeout=30).json()["plan_searches_left"]
before = left()
real_post, real_get = httpx.post, httpx.get  # serpapi.httpx is this same module
serpapi.TTL = timedelta(0)
serpapi.httpx.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("SerpApi blocked: pinned rows only"))
httpx.post = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("Groq blocked: pinned narration only"))

# Current_State.md demo table (verdict, confidence, trust score)
EXPECT = {"airpods-ebay": ("walk_away", None, 5), "airpods-openbox": ("walk_away", None, 0),
          "sony-xm5": ("buy_now", 0.62, 100), "honeywell-heater": ("buy_now", 0.62, None),
          "switch2": ("buy_now", 0.57, None)}
shown = {}
for key, (verdict, conf, score) in EXPECT.items():
    s, meta = contract.build(runner.run(DEMO_CHIPS[key]["url"], source="mcp-test"))
    assert s["verdict"] == verdict and (s["timing"] or {}).get("confidence") == conf, (key, s["verdict"], s["timing"])
    assert score is None or s["trust"]["score"] == score, (key, s["trust"]["score"])
    assert s["safe_to_buy"] == (verdict == "buy_now")
    assert list(s)[0] == "verdict" and "events" not in s
    stages = {e["stage"] for e in meta["events"]}
    if verdict == "walk_away":  # the gate: no timing, in the contract or in the log
        assert not s["timing_ran"] and s["timing"] is None and not stages & {"timing", "lifecycle"}, key
        assert s["summary"].startswith("Clarity: WALK AWAY — do not buy this listing."), s["summary"]
    else:
        assert s["timing_ran"] and "timing" in stages
    assert s["data_as_of"]["listing"].startswith("2026-09"), s["data_as_of"]  # fetch time, not today's replay
    assert s["summary"].endswith(" 2026."), s["summary"]
    assert stages <= {"resolve", "trust", "forensics", "gate", "timing", "lifecycle", "narrator", "verdict"}, stages
    assert meta["events"][-1]["stage"] == "verdict"  # returned at the verdict: no receipts / owners in the log
    if key == "honeywell-heater":  # its price position, not past-year demand
        assert "above the retail median" in s["summary"] and "past years" not in s["summary"], s["summary"]
    size = len(json.dumps(s, ensure_ascii=False))
    print(f"{key:17} {s['verdict']:10} conf {(s['timing'] or {}).get('confidence')} trust {s['trust']['score']:3} | "
          f"structuredContent {size} chars | _meta {len(json.dumps(meta))} chars, {len(meta['events'])} events, "
          f"last at {meta['events'][-1]['t_ms']} ms\n    {s['summary']}")
    shown[key] = (s, size)

# could_not_check: never a pass, never raises
cases = {"empty": "  ", "too long": "x" * 3000, "unsupported site": "https://www.walmart.com/ip/123"}
for name, q in cases.items():
    s, _ = contract.build(runner.run(q, source="mcp-test"))
    assert s["verdict"] == "could_not_check" and not s["safe_to_buy"], (name, s)
    assert list(s)[:3] == ["verdict", "safe_to_buy", "summary"]
    print(f"{name:17} {s['summary']}")
real_stream = orchestrator.stream
orchestrator.stream = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("forced failure"))
s, _ = contract.build(runner.run(DEMO_CHIPS["sony-xm5"]["url"], source="mcp-test"))
assert s["verdict"] == "could_not_check" and not s["safe_to_buy"]
print(f"{'orchestrator fails':17} {s['summary']}")


def slow(*a, **k):
    time.sleep(3)
    yield from ()


orchestrator.stream, runner.TIMEOUT = slow, 1
s, _ = contract.build(runner.run(DEMO_CHIPS["sony-xm5"]["url"], source="mcp-test"))
assert s["verdict"] == "could_not_check" and "didn't finish" in s["reason"], s
print(f"{'timeout':17} {s['summary']}")
orchestrator.stream, runner.TIMEOUT = real_stream, 45

# "What owners say" (app-only tool): the saved automatic run, still with SerpApi and Groq blocked -> 0 searches, 0 Groq.
for key in ("sony-xm5", "switch2"):
    rx = runner.receipts(shown[key][0]["query_id"])
    summary = next(e for e in rx["events"] if e.get("type") == "summary")
    owners = next(e for e in rx["events"] if e["stage"] == "owners")
    assert rx["status"] == "found" and summary["known_issues"] and owners["text"] and not owners.get("fallback"), (key, rx["status"])
    print(f"{key:17} owners: {rx['status']}, {len(summary['known_issues'])} issues, {len(summary['known_praise'])} praise | {owners['text'][:90]}…")
runner.SETTLE = 2
assert runner.receipts(shown["airpods-ebay"][0]["query_id"])["status"] == "error"  # walk-away: receipts never ran
assert runner.receipts("not-a-uuid")["status"] == "error"

for key in ("airpods-ebay", "sony-xm5"):
    s, size = shown[key]
    print(f"\n===== {key}: structuredContent ({size} chars)\n{json.dumps(s, indent=1, ensure_ascii=False)}")

httpx.post, httpx.get = real_post, real_get
after = left()
assert before == after, (before, after)
print(f"\nCONTRACT CHECK PASSED: 5 chips + 5 could_not_check cases + owner receipts; Account API {before} -> {after} searches left")
