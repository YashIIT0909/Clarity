"""Lifecycle weight properties (open decision #11), through timing.run's real verdict logic. 0 SerpApi searches:
a-c stub the other components with synthetic scores; d replays the pinned demo chips with the network blocked.

  a. announced (future date/window) can move a borderline product (timing score within +-0.15) to WAIT
  b. announced cannot override a strongly positive price position on its own
  c. rumored (recent, corroborated) cannot flip any product whose score is outside +-0.05 of the line
  d. released / none / unavailable change nothing: score identical to lifecycle off, on every demo chip

Run: .venv/bin/python test_lifecycle_weights.py
"""
from datetime import date, timedelta

import lifecycle
import serpapi
import timing
import trust
from demo_products import DEMO_CHIPS

serpapi.httpx.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("SerpApi blocked: 0 searches"))
TODAY = date.today()
W = timing.WEIGHTS
h = lambda title, d, src="x": {"title": title, "source": src, "date": f"{d}T12:00:00Z", "link": ""}


def announced(days=None, window=None):
    """A candidate announced 10 days ago, due in `days` (None: no date in the headlines, like the MA2)."""
    d = days is not None and (TODAY + timedelta(days)).isoformat()
    rel = {"status": "announced", "since": None, "release_date": d or None, "release_window": window or (d and d),
           "date_passed": False, "evidence": [{**h("Maker announces X7", TODAY - timedelta(10)), "stage": "announced"}]}
    return {"name": "X7", "release": rel}


RUMOR = {"name": "X7", "release": {"status": "rumored", "release_date": None, "release_window": None, "date_passed": False,
                                   "evidence": [{**h("X7 leak", TODAY - timedelta(d), src), "stage": "rumored"}
                                                for d, src in ((12, "A"), (44, "B"), (75, "C"))]}}  # 3 outlets, 3 months


def verdict(price_score, lc_score):
    """timing.run with demand and maker neutral-but-judged, a price score, and a lifecycle score."""
    timing._trends = lambda q, market=None: []
    timing._maker = lambda brand: (0.0, [], {"ticker": "X", "six_month_change": 0.0})  # judged, neutral
    timing._price = lambda listing, market: (price_score, [], {"position_vs_median": 0.0})
    timing._lifecycle = lambda product, today, emit=None: (lc_score, [], {"status": "stub"})
    r = timing.run({"brand": None}, trust_signals={"listing": {}, "market": {}})
    return r["verdict"], r["signals"]["score"]


real = (timing._trends, timing._maker, timing._price, timing._lifecycle)
tiers = {"due in 20 days": announced(20), "due in 60 days": announced(60), "due in 120 days": announced(120),
         "no date (MA2-like)": announced(None), "due in 300 days": announced(300)}
push = {k: lifecycle.score(c, TODAY)[0] for k, c in tiers.items()}
rumor = lifecycle.score(RUMOR, TODAY)[0]
print(f"weights {W}; announced scores {push}; rumored (recent, corroborated) {rumor}")

# a. Every positive score up to +0.15 flips with a dated announcement within 90 days; report how far each tier reaches.
print("\na. largest buy score each announced tier moves to WAIT:")
for k, s in push.items():
    reach = max((t / 1000 for t in range(1, 451) if verdict(t / 1000 / W["price"], s)[0] == "wait"), default=0)
    print(f"   {k:20} lifecycle {s:+.2f} x {W['lifecycle']} = {s * W['lifecycle']:+.4f} -> flips buy scores up to +{reach:.3f}")
    if k in ("due in 20 days", "due in 60 days"):
        assert reach >= 0.15, k
for t in range(-150, 0, 5):  # a product already at WAIT stays WAIT
    assert verdict(t / 1000 / W["price"], push["due in 20 days"])[0] == "wait"

# b. A strongly positive price (>= 20% below the median: price score >= 0.6) survives the strongest announcement alone.
print("\nb. strongly positive price vs the strongest announcement (due in 20 days):")
for ps in (0.6, 0.8, 1.0):
    v, total = verdict(ps, push["due in 20 days"])
    print(f"   price score {ps:+.1f} ({ps / 3:.0%} below median) -> total {total:+.3f} -> {v}")
    assert v == "buy"
edge = -W["lifecycle"] * min(push.values()) / W["price"]
print(f"   (the line: a price score below {edge:.3f}, about {edge / 3:.1%} under the median, can be flipped)")

# c. A recent, corroborated rumor flips nothing outside +-0.05 of the line.
print("\nc. recent corroborated rumor, total change", f"{rumor * W['lifecycle']:+.3f}:")
flipped = []
for t in [x / 1000 for x in range(-450, 451)]:
    before, _ = verdict(t / W["price"], 0.0)
    after, _ = verdict(t / W["price"], rumor)
    if before != after:
        flipped.append(t)
        assert abs(t) <= 0.05, t
print(f"   scores that flip: {min(flipped):+.3f} .. {max(flipped):+.3f} (all within +-0.05); outside that band: none")

timing._trends, timing._maker, timing._price, timing._lifecycle = real

# d. released / none / unavailable: 0, and identical scores with lifecycle off, on every demo chip (pinned replay).
for status, cand in (("released", {"name": "X7", "release": {**announced(20)["release"], "status": "released",
                                                            "evidence": [{**h("X7 review", TODAY - timedelta(5)), "stage": "released"}]}}),
                     ("rumored (stale)", {"name": "X7", "release": {**RUMOR["release"], "status": "rumored (stale)"}})):
    assert lifecycle.score(cand, TODAY)[0] == 0, status
print("\nd. demo chips, lifecycle on vs off (pinned replay):")
for key, p in DEMO_CHIPS.items():
    t = trust.run(p)
    if t["verdict"] == "fail":
        print(f"   {key:17} trust fail: timing never runs, lifecycle can't touch it")
        continue
    on = timing.run(p, t["signals"])
    timing._lifecycle = lambda product, today, emit=None: (0.0, [], {"status": "off"})
    off = timing.run(p, t["signals"])
    timing._lifecycle = real[3]
    lc = on["signals"]["components"]["lifecycle"]
    same = (on["verdict"], on["confidence"], on["signals"]["score"]) == (off["verdict"], off["confidence"], off["signals"]["score"])
    print(f"   {key:17} lifecycle {lc['status']:11} score on {on['signals']['score']:+.3f} / off {off['signals']['score']:+.3f} "
          f"-> {on['verdict']} {on['confidence']:.0%} {'identical' if same else 'DIFFERENT'}")
    assert lc["status"] in ("released", "none", "unavailable") and lc["score"] == 0 and same, key

# main_reason: the component pushing hardest toward the verdict; a newer model that decided the verdict wins outright.
parts = lambda d, p, m, l, decided=False: {
    "demand": (d, [("dem", "buy now" if d > 0 else "wait")], {}), "price": (p, [("pr", "buy now" if p > 0 else "wait")], {}),
    "maker": (m, [("mk", "buy now" if m > 0 else "wait")], {}),
    "lifecycle": (l, [("lc", "wait")] if l else [], {"decided_verdict": decided})}
assert timing.main_reason(parts(0.5, -0.249, 1.0, 0), "buy")["name"] == "demand"  # the heater: +0.20 vs maker +0.15
assert timing.main_reason(parts(0, 0, 0, 0), "buy") is None
assert timing.main_reason(parts(0.4, -0.7, 0, -1, decided=False), "wait")["name"] == "price"
assert timing.main_reason(parts(0.4, -0.7, 0, -1, decided=True), "wait")["name"] == "lifecycle"
print("\nLIFECYCLE WEIGHT PROPERTIES HOLD (0 SerpApi searches)")
