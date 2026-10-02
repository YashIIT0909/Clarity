"""Parity + failure drills + gate proof, over real HTTP: the web app's /analyze/stream and the MCP tool at
http://localhost:8000/mcp (the official MCP client, Streamable HTTP) must give identical verdicts, trust scores,
timing confidences and reasons for every text chip. The backend runs in this process so SerpApi and Groq can be
blocked as in test_demo_replay.py (TTL 0, pinned rows only): 0 searches, checked against the Account API.
Stop any backend on :8000 first.
  .venv/bin/python -m clarity_mcp.tests.test_parity            (from backend/; ~2 min, the slow drill waits 45 s)
"""
import json
import logging
import os
import re
import threading
import time
from datetime import timedelta

import anyio
import httpx
import uvicorn
from dotenv import load_dotenv

load_dotenv()

import orchestrator  # noqa: E402
import serpapi  # noqa: E402
from clarity_mcp import runner  # noqa: E402
from demo_products import DEMO_CHIPS  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.client import advertise  # noqa: E402
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID  # noqa: E402

logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per Supabase call otherwise
BASE = "http://localhost:8000"
WEB = httpx.Client(timeout=120)  # a Client: the module-level httpx.get/post are blocked below

left = lambda: WEB.get(f"https://serpapi.com/account.json?api_key={os.environ['SERPAPI_KEY']}").json()["plan_searches_left"]
before = left()
real_post, real_get, real_cached, real_stream = httpx.post, httpx.get, serpapi.cached, orchestrator.stream
serpapi.TTL = timedelta(0)
serpapi.httpx.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("SerpApi blocked: pinned rows only"))
httpx.post = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("Groq blocked: pinned narration only"))

import main  # noqa: E402

server = uvicorn.Server(uvicorn.Config(main.app, port=8000, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(60):
    try:
        if WEB.get(f"{BASE}/health").status_code == 200:
            break
    except httpx.TransportError:
        time.sleep(0.5)
else:
    raise SystemExit("backend didn't start on :8000 (is another one running?)")


def web(q):
    """The web app's path: GET /analyze/stream, every SSE event."""
    with WEB.stream("GET", f"{BASE}/analyze/stream", params={"q": q}) as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def mcp(listing, timeout=None):
    """The agent's path: a real MCP client (advertising MCP Apps) calling clarity_check_listing over HTTP."""
    async def call():
        async with Client(f"{BASE}/mcp", extensions=[advertise(EXTENSION_ID, {"mimeTypes": [APP_MIME_TYPE]})]) as c:
            return await c.call_tool("clarity_check_listing", {"listing": listing}, read_timeout_seconds=timeout)
    return anyio.run(call)


def same_run(reasons):
    """Trust's price-history line counts every earlier run of the product, and each run adds one (Current_State open
    decision #3), so the web and MCP runs, seconds apart, differ by exactly that count. Everything else must match."""
    return reasons and [re.sub(r"across \d+ earlier checks", "across N earlier checks", r) for r in reasons]


def one(events, stage, status="done"):
    return next((e for e in events if e.get("stage") == stage and e.get("status") == status), None)


def couldnt(r, why):
    s = r.structured_content
    assert s["verdict"] == "could_not_check" and s["safe_to_buy"] is False, s
    assert list(s)[:3] == ["verdict", "safe_to_buy", "summary"] and why in s["summary"], s["summary"]
    assert r.content[0].text == s["summary"]
    return s["summary"]


# 1. Parity, every text chip, same input (the chip's URL) on both paths.
for key, chip in DEMO_CHIPS.items():
    w = web(chip["url"])
    r = mcp(chip["url"])
    s, log = r.structured_content, r.meta["events"]
    wv, wres = one(w, "verdict"), w[-1]["result"]
    verdict = "buy_timing_not_judged" if wv["final_verdict"] == "buy_now" and wv["timing_judged"] is False else wv["final_verdict"]
    assert s["verdict"] == verdict, (key, s["verdict"], verdict)
    assert s["trust"]["score"] == wres["trust"]["signals"]["score"], key
    assert (s["timing"] or {}).get("confidence") == wv["confidence"], key
    assert s["reasons"] == [f"{x['title']}: {x['text']}" for x in one(w, "narrator")["steps"]][:5], key
    for stage in ("trust", "timing"):
        assert same_run((one(log, stage) or {}).get("reasons")) == same_run((one(w, stage) or {}).get("reasons")), (key, stage)
    assert s["query_id"] != wres["query_id"]  # two real runs, both in the history
    print(f"PARITY {key:17} {s['verdict']:10} trust {s['trust']['score']:3} conf {(s['timing'] or {}).get('confidence')}"
          f" | reasons identical ({len(s['reasons'])} narrator steps, trust/timing step reasons)")

    # 3. Gate proof through MCP: no timing, in the contract or in the view's log.
    if s["verdict"] == "walk_away":
        stages = {e["stage"] for e in log}
        assert s["timing_ran"] is False and s["timing"] is None and not stages & {"timing", "lifecycle"}, key
        assert one(log, "gate")["decision"] == "stop"
        print(f"GATE   {key:17} timing_ran=false, no timing/lifecycle events, gate=stop")

# 2. Failure drills: could_not_check with safe_to_buy false; never a crash, never a pass.
sony = DEMO_CHIPS["sony-xm5"]["url"]
for name, q, why in [("malformed URL", "https://www.amazon.com/dp/", "are supported"),
                     ("unsupported domain", "https://www.walmart.com/ip/123456", "are supported"),
                     ("empty input", "   ", "no listing was given")]:
    print(f"DRILL  {name:20} {couldnt(mcp(q), why)}")


def raises(*a, **k):
    raise RuntimeError("orchestrator down (drill)")
    yield


orchestrator.stream = raises
print(f"DRILL  {'orchestrator raises':20} {couldnt(mcp(sony), 'orchestrator down')}")


def slow(*a, **k):
    time.sleep(runner.TIMEOUT + 5)
    raise RuntimeError("too late")
    yield


orchestrator.stream = slow
t = time.monotonic()
print(f"DRILL  {'slower than 45 s':20} {couldnt(mcp(sony), f'within {runner.TIMEOUT} s')} ({time.monotonic() - t:.0f} s)")


async def cancelled():  # the host gives up mid-call (basic-host does at 60 s): the client cancels the request
    async with Client(f"{BASE}/mcp") as c:
        with anyio.move_on_after(3) as scope:
            await c.call_tool("clarity_check_listing", {"listing": sony})
        return scope.cancelled_caught


assert anyio.run(cancelled), "the call should have been cancelled"
orchestrator.stream = real_stream
after_cancel = mcp(sony).structured_content
assert after_cancel["verdict"] == "buy_now" and after_cancel["safe_to_buy"]
print(f"DRILL  {'host cancels':20} client cancelled after 3 s; server fine, next call: {after_cancel['verdict']}"
      " (the view's \"Couldn't check\" card is the basic-host drill)")

# Groq unavailable: the narration cache misses and Groq is blocked -> rule-based narration, real verdict.
serpapi.cached = lambda engine, *a, **k: None if engine == "groq_narrator" else real_cached(engine, *a, **k)
r = mcp(sony)
serpapi.cached = real_cached
s, story = r.structured_content, one(r.meta["events"], "narrator")
assert story.get("fallback") and "Groq blocked" in story.get("error", ""), story
assert s["verdict"] == "buy_now" and s["safe_to_buy"] and s["timing"]["confidence"] == 0.62, s["verdict"]
print(f"DRILL  {'Groq unavailable':20} rule-based narration (fallback), real verdict: {s['summary']}")

server.should_exit = True
httpx.post, httpx.get = real_post, real_get
after = left()
assert before == after, (before, after)
print(f"\nPARITY + DRILLS PASSED: {len(DEMO_CHIPS)} chips identical on /analyze/stream and /mcp, 7 drills, gate proven; "
      f"Account API {before} -> {after} searches left")
