"""Timing agent: demand trend + price position + maker signal + lifecycle (successor radar) + owner receipts -> buy/wait
with confidence. Rule-based, no ML.

Each signal scores in [-1, 1]: positive = buy now, negative = wait. Weighted sum decides the verdict;
its magnitude sets the confidence.
"""
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

import alternatives
import lifecycle
import locales
import trust
from demo_products import BRAND_TICKERS
from events import EVENTS, next_event, starts
from locales import money
from serpapi import search

# lifecycle only ever pushes toward wait (score in [-1, 0]); it is added on top, so at 0 every other score is unchanged.
# 0.25: an announcement due within 30 days (-1.0) moves a borderline product to wait, but can't outweigh a strongly
# positive price alone (+1.0 x 0.45). Signed off 2026-09-30 (open decision #11): properties in test_lifecycle_weights.py.
# receipts, like lifecycle, only pushes toward wait (score in [-1, 0]). 0.10: corroborated defects owners report flip
# only a buy at 55% confidence or less, and never a strongly positive price. Signed off 2026-10-04:
# properties in test_receipts_weight.py.
WEIGHTS = {"demand": 0.40, "price": 0.45, "maker": 0.15, "lifecycle": 0.25, "receipts": 0.10}
# Per counted card. Criticisms (design, price) and praise are permanent traits, not a reason to wait: 0.
RECEIPTS_SCORE = {"problem": 1.0, "concern": 0.5, "forum": 0.5}
RECEIPTS_WAIT = 8  # s. Cache-only receipts: a miss returns in ~0.2 s, a full cached replay in 3-5 s (22 cache reads)
NOT_JUDGED = "Nothing found suggests waiting, but we couldn't judge timing for this product"
SEASON_AHEAD_WEEKS = 8
MIN_SEASON_BASE = 5          # a past year counts only if its average interest is >= this (product existed)


def pull(x, dead_zone):
    """Direction a signal value pushes the decision: positive -> buy now."""
    return "neutral" if abs(x) <= dead_zone else ("buy now" if x > 0 else "wait")


def clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def _trends(query, market=None):
    """Google Trends, 5 years weekly. [] when Trends has nothing for the term (cached as a no-results answer).
    US: worldwide, as it always was (no geo: US cache keys unchanged). India: geo=IN."""
    geo = locales.get(market)["trends_geo"]
    d = search("google_trends", q=query, data_type="TIMESERIES", date="today 5-y", **({"geo": geo} if geo else {}))
    return (d.get("interest_over_time") or {}).get("timeline_data") or []


def _demand(t, query, category, today, market=None):
    """Recent momentum + what happened in the next 8 weeks in past years + interest around the next retail event.
    The event part is context only (not scored): no price data backs a buy/wait direction for it."""
    v = [p["values"][0]["extracted_value"] for p in t]

    recent, prior = statistics.mean(v[-4:]), statistics.mean(v[-12:-4])
    momentum = (recent - prior) / max(prior, 1)

    # Same week in each prior year (52-week steps back), and the 8 weeks after it.
    # Skip years where the product barely existed (its whole surrounding year is near zero); a low
    # off-season value in an otherwise active year is exactly the seasonal signal we want.
    ahead = []
    for back in (52, 104, 156, 208):
        i = len(v) - 1 - back
        year = v[max(i - 26, 0) : i + 26]
        if i >= 0 and v[i] >= 1 and statistics.mean(year) >= MIN_SEASON_BASE:
            ahead.append(min(statistics.mean(v[i + 1 : i + 1 + SEASON_AHEAD_WEEKS]) / v[i], 4))
    seasonal = statistics.mean(ahead) if ahead else None

    score = 0.5 * clip(momentum * 2) + (0.5 * clip(seasonal - 1) if seasonal else 0)
    if seasonal is None:
        score *= 2  # momentum alone
    reasons = [(f"Search interest {'up' if momentum >= 0 else 'down'} {abs(momentum) * 100:.0f}% over the last 4 weeks vs the 8 before",
                pull(momentum, 0.05))]
    if seasonal is not None:
        reasons.append((
            # One average over N earlier years, never a span: "(2 yrs)" was narrated as "in the past two years".
            f"Over the {SEASON_AHEAD_WEEKS} weeks after this date, search interest {'rose' if seasonal >= 1 else 'fell'} "
            f"{abs(seasonal - 1) * 100:.0f}% on average, based on {len(ahead)} earlier year{'s' if len(ahead) != 1 else ''}",
            pull(seasonal - 1, 0.05),
        ))
    event_reasons, event = _event(t, query, category, today, market)
    return clip(score), reasons + event_reasons, {"query": query, "recent_avg": recent, "prior_avg": prior,
                                                  "momentum": round(momentum, 3), "seasonal_ratio": seasonal and round(seasonal, 3),
                                                  "seasons_used": len(ahead), "event": event}


def _event(t, query, category, today, market=None):
    """Next retail event in range (in this market) + how search interest moved around its past occurrences.
    Demand side only: SerpApi has no price history, so no price change is claimed here."""
    ev = next_event(today, category, market=market)
    if not ev:
        return [], None
    name, days = ev
    weeks = [(datetime.fromtimestamp(int(p["timestamp"]), timezone.utc).date(), p["values"][0]["extracted_value"]) for p in t]
    changes = {}
    for s in EVENTS[name]["dates"].values():
        d = s and date.fromisoformat(s)
        if not d or d >= today:
            continue
        # Trends weeks are keyed by start date: the week containing the event + the week after, vs the 4 weeks before.
        around = [v for w, v in weeks if d - timedelta(7) < w <= d + timedelta(7)]
        before = [v for w, v in weeks if d - timedelta(35) < w <= d - timedelta(7)]
        year = [v for w, v in weeks if abs((w - d).days) <= 182]  # same "product existed" test as _demand
        if len(around) == 2 and len(before) == 4 and statistics.mean(before) >= 1 and statistics.mean(year) >= MIN_SEASON_BASE:
            changes[d.year] = round(statistics.mean(around) / statistics.mean(before) - 1, 3)
    signals = {"name": name, "days_away": days, "interest_change_by_year": changes}
    if not changes:
        return [(f"{starts(name, days)}; not enough past search data around it to compare; context only, not scored",
                 "neutral")], signals
    avg = statistics.mean(changes.values())
    years = ", ".join(f"{y} {c * 100:+.0f}%" for y, c in changes.items())
    return [(
        f"{starts(name, days)}; in past years search interest for \"{query}\" "
        f"{'rose' if avg >= 0 else 'fell'} {abs(avg) * 100:.0f}% on average in the two weeks around it "
        f"vs the 4 weeks before ({years}); context only, not scored",
        "neutral",
    )], {**signals, "avg_interest_change": round(avg, 3)}


def _price(listing, market):
    """Where the listing sits in today's retail price spread, and how much of the market is on sale."""
    median, price = market["median"], listing["price"]
    if price is None:  # screenshot without a price entered
        return 0.0, [("No price given, so price position wasn't judged", "neutral")], {}
    if not median:
        return 0.0, [("Not enough retail prices to judge price position", "neutral")], {}
    position = (price - median) / median
    below = sum(p["price"] < price for p in market["stores"]) if market["stores"] else None
    score = clip(-position * 3)  # 10% under median -> +0.3; 33%+ over -> -1
    rel = "at" if abs(position) <= 0.05 else f"{abs(position) * 100:.0f}% {'above' if position > 0 else 'below'}"
    usual = "the retail median" if market.get("basis", "new") == "new" else f"the usual price for {market['basis']} units"
    mkt = listing.get("market")
    reasons = [(f"{money(price, mkt)} is {rel} {usual} of {money(median, mkt)}", pull(-position, 0.05))]
    if below:
        reasons.append((f"{below} of {len(market['stores'])} retailers on the product page are cheaper right now", "wait"))
    return score, reasons, {"position_vs_median": round(position, 3), "cheaper_retailers": below}


def _maker(brand):
    """Maker's 6-month stock trend. Weak signal: a strong maker is less likely to be cutting prices."""
    ticker = BRAND_TICKERS.get(brand)
    if not ticker:
        return 0.0, [], {"ticker": None}
    g = search("google_finance", q=ticker, window="6M").get("graph") or []
    if len(g) < 2:
        return 0.0, [], {"ticker": ticker}
    change = (g[-1]["price"] - g[0]["price"]) / g[0]["price"]
    score = clip(change * 2)
    return score, [(f"{ticker.split(':')[0]} stock {'up' if change >= 0 else 'down'} {abs(change) * 100:.0f}% over 6 months", pull(change, 0.03))], {
        "ticker": ticker, "six_month_change": round(change, 3)}


FOUND = {"trends": lambda d: len((d.get("related_queries") or {}).get("rising") or []),
         "autocomplete": lambda d: len(d.get("suggestions") or []),
         "news": lambda d: len(lifecycle.news_rows(d))}


def _lifecycle(product, today, emit=None):
    """Successor radar: Trends rising queries + Google Autocomplete + Google News, in parallel through serpapi_cache.
    Any engine error or timeout -> unavailable. No model identifier in the name (a dumbbell, "space heater") ->
    unavailable with no searches. Unavailable, none, released and stale rumors all score 0.
    emit(event): live progress for the UI (start, engine_done, candidates, status), like forensics."""
    def ev(**e):
        if emit:
            try:
                emit(e)
            except Exception:  # a UI problem never breaks the check
                pass

    def done(score, reasons, signals, top=None):
        rel = top and top["release"]
        ev(type="status", name=top and top["name"], status=signals["status"],
           date=rel and (rel["release_date"] or rel["since"]), window=rel and rel["release_window"],
           # headlines at the current status first (newest first within each), then the earlier stages
           headlines=sorted((rel or {}).get("evidence", []), key=lambda e: not signals["status"].startswith(e["stage"]))[:3],
           why=signals.get("why"))
        return score, reasons, signals

    brand = (product.get("brand") or "").split()[0] if product.get("brand") else None
    name = product.get("trend_query")
    if not (name and lifecycle.parse_model(name, brand)):
        ev(type="start", engines=[])
        return done(0.0, [], {"status": "unavailable", "why": "no model identifier in the product name"})
    calls = lifecycle.queries(name, product.get("market"))
    ev(type="start", engines=[engine for engine, _ in calls.values()])
    start = time.monotonic()
    ms = lambda: round((time.monotonic() - start) * 1000)
    pool = ThreadPoolExecutor(len(calls))
    jobs = {pool.submit(search, engine, **params): k for k, (engine, params) in calls.items()}
    data, failed = {}, {}
    try:
        for job in as_completed(jobs, timeout=lifecycle.TIMEOUT):
            k = jobs[job]
            try:
                data[k] = job.result()
                ev(type="engine_done", engine=calls[k][0], found=FOUND[k](data[k]), ms=ms())
            except Exception as e:  # noqa: BLE001 — an engine that errors makes lifecycle unavailable
                failed[k] = f"{type(e).__name__}: {e}"[:160]
                ev(type="engine_done", engine=calls[k][0], found=0, ms=ms(), unavailable=failed[k])
    except TimeoutError:
        for k in set(calls) - set(data) - set(failed):
            failed[k] = f"timed out after {lifecycle.TIMEOUT}s"
            ev(type="engine_done", engine=calls[k][0], found=0, ms=ms(), unavailable=failed[k])
    pool.shutdown(wait=False, cancel_futures=True)
    if failed:
        return done(0.0, [], {"status": "unavailable", "why": "engine failed", "engines_failed": failed})

    display = lifecycle.search_term(product.get("label") or name, brand) or name  # "Sony WH-1000XM5", not lowercase
    news = lifecycle.news_rows(data["news"])
    found = lifecycle.find_successor_candidates(
        display, (data["trends"].get("related_queries") or {}).get("rising") or [],
        [s["value"] for s in data["autocomplete"].get("suggestions") or []], news, brand=brand)
    ev(type="candidates", candidates=[
        {k: c[k] for k in ("name", "identifier", "sources", "trends_value")}
        | {"evidence": [{k: e.get(k) for k in ("source", "text", "value")} for e in c["evidence"][:6]]}
        for c in found["candidates"]])
    signals = {"status": found["status"], "search_term": name, **{k: found[k] for k in ("sources_used", "context")},
               "candidates": found["candidates"]}
    if found["status"] != "found":
        return done(0.0, [], signals)
    scored = []
    for c in found["candidates"]:
        c["release"] = lifecycle.classify_release_status(c, news, today)
        scored.append((*lifecycle.score(c, today), c))
    s, reason, top = min(scored, key=lambda x: x[0])  # the strongest push toward wait
    signals.update(status=top["release"]["status"], successor=top["name"])
    return done(s, [(reason, "wait" if s < 0 else "neutral")] if reason else [], signals, top)


def _receipts(owners):
    """What owners say, as a wait-only signal. owners: the cache-only receipts.run future (started before timing), or
    None. Counts only corroborated defects: a known problem (2+ independent sources report it) -1.0, a known concern
    (2+ sources, hedged) or a forum-reported issue (3+ threads) -0.5; capped at -1. Anything else (none, unavailable,
    on request, a timeout, praise or criticism only) is 0: missing data is never a signal."""
    try:
        r = owners.result(timeout=RECEIPTS_WAIT) if owners else None
    except Exception:  # noqa: BLE001 — a timeout (or any failure) is absence, never a penalty
        return 0.0, [], {"status": "timed out"}
    if not r or r.get("status") != "found":
        return 0.0, [], {"status": r and r.get("status")}
    counted = [(c["type"], c) for c in r["known_issues"] if c["type"] in RECEIPTS_SCORE] + \
              [("forum", c) for c in r["forum_reported"]]
    text = {"problem": lambda c: f"Known problem from owners and reviewers: {c['label_display']} ({c['source_count']} independent sources report it)",
            "concern": lambda c: f"Possible risk raised by reviewers: {c['label_display']} ({c['source_count']} independent sources)",
            "forum": lambda c: f"Reported by owners in forums: {c['label_display']} ({c['thread_count']} threads)"}
    score = -min(1.0, sum(RECEIPTS_SCORE[k] for k, _ in counted))
    return score, [(text[k](c), "wait") for k, c in counted], {
        "status": "found", "counted": [{"type": k, "label": c["label_display"]} for k, c in counted]}


def _wait_window(product, parts, today):
    """When to check again, the first that applies: an announced newer model's release, the next retail event, a
    seasonal fall in search interest. None otherwise: never invented. A check-again time, never a price forecast
    (SerpApi has no price history to back one)."""
    lc = parts["lifecycle"][2]
    if lc.get("status") == "announced":
        rel = next(c["release"] for c in lc["candidates"] if c["name"] == lc["successor"])
        if not rel["date_passed"] and (rel["release_date"] or rel["release_window"]):
            d = rel["release_date"] and date.fromisoformat(rel["release_date"])
            days = d and (d - today).days
            when = f"due {rel['release_window']}" + (f", in {days} day{'s' if days != 1 else ''}" if days else "")
            return {"kind": "successor", "date": rel["release_date"], "days": days,
                    "text": f"Check again when the {lc['successor']} is out ({when}): you can then choose between "
                            f"both models."}
    ev = next_event(today, product.get("category"), market=product.get("market"))
    if ev:
        name, days = ev
        return {"kind": "event", "date": (today + timedelta(days)).isoformat(), "days": days,
                "text": f"{starts(name, days)}: a good time to check again. Clarity "
                        f"can't predict sale prices."}
    seasonal = parts["demand"][2].get("seasonal_ratio")
    if seasonal is not None and pull(seasonal - 1, 0.05) == "wait":
        return {"kind": "season", "date": (today + timedelta(weeks=SEASON_AHEAD_WEEKS)).isoformat(),
                "days": SEASON_AHEAD_WEEKS * 7,
                "text": f"In past years search interest fell over the next {SEASON_AHEAD_WEEKS} weeks: check again "
                        f"then."}
    return None


def wait_plan(product, listing, market, parts, today):
    """What a WAIT waits for: a target price and when to check again, each with its reason; either can be None.
    Target = today's cheapest trusted store (alternatives.best) when it's cheaper than this listing: a price someone
    charges now, not a hoped-for one. Otherwise a good price (median - 1 sigma). Never below the too_cheap line
    (median - 2 sigma: a lower price would itself be flagged; a trusted store is inside the band by definition), and only
    when it's below this listing's price. None when there's nothing to say."""
    target, mkt = None, listing.get("market")
    price, median, sigma = listing.get("price"), market.get("median"), market.get("sigma")
    if price is not None and median and sigma:
        floor = median - trust.Z_TOO_CHEAP * sigma
        good = max(floor, float(int(median - sigma)))  # whole units: "$310 or less" / "₹18,500", not "$310.42"
        store = alternatives.best(listing, market)
        usual = "the new-retail median" if market.get("basis", "new") == "new" else f"the usual price for {market['basis']} units"
        if store and store["price"] < price:
            target = {"price": store["price"], "basis": "store",
                      "reason": f"{store['store']} sells it for {money(store['price'], mkt)} today"}
        else:
            target = {"price": good, "basis": "band",
                      "reason": f"a good price: one typical price spread ({money(sigma, mkt)}) below {usual} of {money(median, mkt)}"}
        if target["price"] >= price:  # this listing is already at or under it: the wait is about something else
            target = None
    window = _wait_window(product, parts, today)
    if not (target or window):
        return None
    if target:
        target["price_text"] = money(target["price"], mkt)
    lines = ([f"Wait for {target['price_text']} or less ({target['reason']})."] if target else []) + \
            ([window["text"]] if window else [])
    return {"target": target, "window": window, "text": " ".join(lines)}


def main_reason(parts, verdict):
    """The component pushing hardest toward the verdict (weight x score), as a fact for the narrator: {name,
    contribution, text}. A newer model that decided the verdict is the main reason by definition. None when nothing
    pushes toward the verdict (timing not judged, or a 0.0 tie). Owner receipts that decided it, likewise (newer model first)."""
    toward = 1 if verdict == "buy" else -1
    contrib = {k: WEIGHTS[k] * parts[k][0] for k in parts}
    k = next((k for k in ("lifecycle", "receipts") if k in parts and parts[k][2].get("decided_verdict")), None) or \
        max(contrib, key=lambda k: toward * contrib[k])
    if toward * contrib[k] <= 0:
        return None
    lines = parts[k][1]
    text = next((t for t, p in lines if p == ("buy now" if toward > 0 else "wait")), lines[0][0] if lines else k)
    return {"name": k, "contribution": round(contrib[k], 3), "text": text}


def run(product: dict, trust_signals: dict | None = None, emit=None, owners=None) -> dict:
    """product: demo-product dict. trust_signals: reuse trust agent's listing/market (else re-fetched, cached).
    emit(event): live lifecycle progress (the orchestrator streams it as {"stage": "lifecycle", ...}).
    owners: the cache-only receipts.run future, read last (it ran beside the other parts); None -> receipts score 0."""
    if trust_signals is None:
        trust_signals = trust.run(product)["signals"]
    listing, market = trust_signals["listing"], trust_signals["market"]

    # No search term, or Trends has no data / under 12 weeks for it (a niche non-demo product) -> demand not judged,
    # never an exception. category: hand-written for demo products only; missing = unknown.
    t = _trends(product["trend_query"], product.get("market")) if product.get("trend_query") else []
    parts = {
        "demand": (_demand(t, product["trend_query"], product.get("category"), date.today(), product.get("market"))
                   if len(t) >= 12 else
                   (0.0, [("Not enough search-interest data for this product, so demand wasn't judged", "neutral")], {})),
        "price": _price(listing, market),
        "maker": _maker(product.get("brand")),  # no brand or no ticker -> neutral
        "lifecycle": _lifecycle(product, date.today(), emit),
        "receipts": _receipts(owners),
    }
    total = sum(WEIGHTS[k] * parts[k][0] for k in parts)
    # A component is judged when it had data to score (its signals carry the measured value).
    judged = {"demand": "momentum" in parts["demand"][2], "price": "position_vs_median" in parts["price"][2],
              "maker": "six_month_change" in parts["maker"][2],
              # released / none / stale is context, not evidence either way: counts only when it pushes
              "lifecycle": parts["lifecycle"][0] != 0, "receipts": parts["receipts"][0] != 0}
    reasons = [text for k in parts for text, _ in parts[k][1]]
    if not any(judged.values()):
        # Nothing to judge is not a tie: 0.0 must not become WAIT. Trust passed and nothing suggests waiting -> buy,
        # with no confidence figure. (A real tie, evidence present and score exactly 0, still goes to wait below.)
        verdict, confidence = "buy", None
        reasons.append(NOT_JUDGED)
    else:
        verdict = "buy" if total > 0 else "wait"
        confidence = round(0.5 + min(abs(total), 0.9) / 2, 2)  # 0.50 .. 0.95

    # Lifecycle (or receipts) decided the verdict when the other signals alone say buy and it tips the total to wait.
    for k in ("lifecycle", "receipts"):
        parts[k][2]["decided_verdict"] = verdict == "wait" and total - WEIGHTS[k] * parts[k][0] > 0
    # What to wait for: information only, after the verdict is fixed (never scored, never narrated).
    plan = wait_plan(product, listing, market, parts, date.today()) if verdict == "wait" else None

    return {
        "verdict": verdict,
        "confidence": confidence,
        "main_reason": main_reason(parts, verdict),
        "judged": any(judged.values()),
        "reasons": reasons,
        "evidence": [{"signal": k, "fact": text, "pulls_toward": p} for k in parts for text, p in parts[k][1]],
        "wait_plan": plan,
        "signals": {
            "score": round(total, 3),
            "components": {k: {"score": round(parts[k][0], 3), "weight": WEIGHTS[k], "judged": judged[k], **parts[k][2]}
                           for k in parts},
            "judged": any(judged.values()),
            "as_of": date.today().isoformat(),
            "wait_plan": plan,
        },
    }


if __name__ == "__main__":
    import json
    import sys

    from demo_products import DEMO_CHIPS, DEMO_PRODUCTS

    names = [a for a in sys.argv[1:] if not a.startswith("-")] or list(DEMO_CHIPS)
    mismatches = []
    for name in names:
        p = DEMO_PRODUCTS[name]
        r = run(p)
        print(f"\n=== {name}  ->  {r['verdict'].upper()} ({r['confidence']:.0%})  (expected {p.get('expect_timing')})  score {r['signals']['score']}")
        for k, c in r["signals"]["components"].items():
            print(f"  [{k:6} {c['score']:+.2f} x{c['weight']}]")
        for reason in r["reasons"]:
            print(f"  - {reason}")
        if "-v" in sys.argv:
            print(json.dumps(r["signals"], indent=2))
        if p.get("expect_timing") and r["verdict"] != p["expect_timing"]:
            mismatches.append(name)
    print("\nMISMATCHES vs Phase 0:", mismatches or "none")
    sys.exit(1 if mismatches else 0)
