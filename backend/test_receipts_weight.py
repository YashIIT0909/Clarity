"""Receipts weight properties (signed off 2026-10-04), through timing.run's real verdict logic. a-d stub the other
components with synthetic scores; e replays the demo chips through the orchestrator, cache-only.

  a. a known problem moves a borderline buy (timing score <= +0.10) to WAIT, and nothing above it
  b. any number of known problems cannot override a strongly positive price on its own
  c. absence is exactly 0: none / unavailable / on request / Groq limit / timeout / praise, criticism, single-source only
  d. never pushes toward buy: the total with receipts is never higher than without
  e. demo chips: switch2 (a known concern) -0.05; every other chip identical with receipts off; walk-aways run no receipts

0 SerpApi searches and 0 Groq calls (narrator on its rule-based fallback; receipts cache-only). e writes to Supabase
like test_gate.py.
  .venv/bin/python test_receipts_weight.py
"""
from concurrent.futures import Future

import narrator
import orchestrator
import receipts
import serpapi
import timing
from demo_products import DEMO_CHIPS

serpapi.httpx.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("SerpApi blocked in this test"))
narrator.httpx.post = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("no LLM in this test"))
receipts.httpx.post = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("no LLM in this test"))
W = timing.WEIGHTS["receipts"]


def card(type_, kind="issue", n=3):
    return {"kind": kind, "type": type_, "label_display": f"{type_} {n}", "source_count": n, "thread_count": n}


def found(issues=(), praise=(), forum=(), once=()):
    return {"status": "found", "known_issues": list(issues), "known_praise": list(praise), "forum_reported": list(forum),
            "mentioned_once": list(once), "forum_only": []}


def done(r):
    f = Future()
    f.set_result(r)
    return f


PROBLEM, CONCERN, FORUM = found([card("problem")]), found([card("concern")]), found(forum=[card("problem")])
LOUD = found([card("problem") for _ in range(9)], forum=[card("problem") for _ in range(3)])


def verdict(price_score, owners):
    """timing.run with demand, maker and lifecycle neutral, a price score, and a receipts result (or None)."""
    timing._trends = lambda q, market=None: []
    timing._maker = lambda brand: (0.0, [], {"ticker": "X", "six_month_change": 0.0})  # judged, neutral
    timing._price = lambda listing, market: (price_score, [], {"position_vs_median": 0.0})
    timing._lifecycle = lambda product, today, emit=None: (0.0, [], {"status": "stub"})
    r = timing.run({"brand": None}, trust_signals={"listing": {}, "market": {}},
                   owners=owners if owners is None or isinstance(owners, Future) else done(owners))
    return r["verdict"], r["signals"]["score"], r


real = (timing._trends, timing._maker, timing._price, timing._lifecycle)
grid = [t / 1000 for t in range(-450, 451)]  # every timing total from the other signals, via price

# a. A problem (-1.0 x 0.10) flips exactly the buys at or under +0.10; a concern / forum report (-0.5) those under +0.05.
for owners, reach in ((PROBLEM, W), (CONCERN, W / 2), (FORUM, W / 2), (LOUD, W)):
    flipped = [t for t in grid if verdict(t / 0.45, None)[0] == "buy" and verdict(t / 0.45, owners)[0] == "wait"]
    assert flipped and max(flipped) <= reach + 1e-9 and max(flipped) >= reach - 0.001, (owners["known_issues"][:1], max(flipped))
_, _, r = verdict(0.05 / 0.45, PROBLEM)
assert r["main_reason"]["name"] == "receipts" and r["signals"]["components"]["receipts"]["decided_verdict"], r["main_reason"]
assert abs(r["confidence"] - 0.525) < 0.01 and r["evidence"][-1]["pulls_toward"] == "wait"
print(f"a. a known problem flips buys up to +{W:.2f} (confidence <= {0.5 + W / 2:.0%}); a concern or forum report up to +{W / 2:.2f}")

# b. Strongly positive price (score >= 0.6, 20%+ under median) survives the loudest result.
for ps in (0.6, 0.8, 1.0):
    assert verdict(ps, LOUD)[0] == "buy", ps
print(f"b. price score +0.6..+1.0 stays BUY against {len(LOUD['known_issues'])} problems + 3 forum reports (cap -1.0)")

# c. Absence is exactly 0.
hung = Future()  # never completes: the timeout path
timing.RECEIPTS_WAIT = 0.2
absent = {"none": {**found(), "status": "none"}, "unavailable": {**found(), "status": "unavailable"},
          "on_request": {"status": "on_request"}, "groq limit": {**found(), "status": "unavailable", "retry": True},
          "praise only": found(praise=[card("praise", "praise")]), "criticism only": found([card("criticism")]),
          "single source": found(once=[card("problem", n=1)]), "timed out": hung}
for name, owners in absent.items():
    for t in (-0.3, -0.02, 0.0, 0.02, 0.3):
        off, on = verdict(t / 0.45, None)[2], verdict(t / 0.45, owners)[2]
        same = lambda r: (r["verdict"], r["confidence"], r["signals"]["score"], r["main_reason"], r["judged"])
        assert same(off) == same(on), (name, t)
        assert on["signals"]["components"]["receipts"]["score"] == 0 and not on["signals"]["components"]["receipts"]["judged"]
timing.RECEIPTS_WAIT = 8
print(f"c. {', '.join(absent)}: identical to receipts off")

# d. Never toward buy.
for owners in (PROBLEM, CONCERN, FORUM, LOUD):
    assert all(verdict(t / 0.45, owners)[1] <= verdict(t / 0.45, None)[1] for t in grid[::25])
print("d. never raises the total")

timing._trends, timing._maker, timing._price, timing._lifecycle = real

# e. Real demo chips, through the orchestrator (receipts cache-only, as in the app).
print("e. demo chips, receipts on vs off:")
for key, p in DEMO_CHIPS.items():
    off = orchestrator.run(p, label=f"receipts-weight-test:{key}:off", with_receipts=False)
    on = orchestrator.run(p, label=f"receipts-weight-test:{key}:on")
    if off["trust"]["verdict"] == "fail":
        assert on["receipts"] is None and on["timing"] is None, key
        print(f"   {key:17} walk-away: no receipts, no timing")
        continue
    a, b = off["timing"]["signals"]["score"], on["timing"]["signals"]["score"]
    rc = on["timing"]["signals"]["components"]["receipts"]
    expect = -W * 0.5 if key == "switch2" else 0.0  # its joystick drift concern; xm5 has criticisms only
    assert abs((b - a) - expect) < 0.001, (key, a, b, rc)
    print(f"   {key:17} receipts {rc['status']:11} {rc['score']:+.2f} -> score {a:+.3f} / {b:+.3f}, "
          f"{off['final_verdict']} {off['confidence']:.0%} / {on['final_verdict']} {on['confidence']:.0%}")
print("\nRECEIPTS WEIGHT PROPERTIES HOLD (0 SerpApi searches, 0 Groq calls)")
