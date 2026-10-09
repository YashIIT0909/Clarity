"""Orchestrator: trust gates timing, then the narrator writes it up. Yields each step as it happens.
"What owners say" (receipts) runs beside timing once trust passes, cache-only. Timing reads it as a small wait-only
signal (weight 0.10: corroborated defects only; anything missing scores 0). Its full evidence and narration still
arrive after the verdict; a live on-request run later never re-scores a verdict already shown."""
import logging
import queue
import time
from concurrent.futures import ThreadPoolExecutor

import alternatives
import locales
import narrator
import receipts
import timing
import trust
from serpapi import cached, db

log = logging.getLogger("orchestrator")
# query_id -> what a live "See what owners say" run needs. ponytail: in-memory, single process, like main.JOBS; a
# multi-worker deploy needs a shared store.
RECEIPTS_ON_REQUEST: dict[str, dict] = {}
ON_REQUEST_TTL = 3600  # s

VERDICT = {"buy": "buy_now", "wait": "wait"}


def _live(fn, stage, *args):
    """Run fn(*args, emit) in a thread, yielding its progress events live as {"stage": stage, ...}; returns its result."""
    live = queue.Queue()
    with ThreadPoolExecutor(1) as pool:
        job = pool.submit(fn, *args, lambda e: live.put({"stage": stage, **e}))
        while not job.done() or not live.empty():
            try:
                yield live.get(timeout=0.1)
            except queue.Empty:
                pass
        return job.result()


def _receipts_args(product, t):
    listing = t["signals"].get("listing") or {}
    amazon = (cached("amazon_product", asin=product["listing_id"], **locales.get(product.get("market"))["amazon"])
              if product.get("marketplace") == "amazon" else None)
    return product, listing, amazon


def _owners_events(query_id, product, r):
    """The receipts summary event, then the separate "What owners say" narrator step (after the verdict, never part
    of it). Saves the evidence and the narration."""
    yield {"stage": "receipts", "type": "summary", "query_id": query_id, **r}
    story = narrator.owners(r, r.get("name") or product.get("label") or "this product")
    yield {"stage": "owners", "status": "done", **story}
    _save_receipts(query_id, {**r, "narration": story})


def _save_receipts(query_id, r):
    """Full evidence in receipts_results (one row per query). A run that didn't happen (on_request) isn't saved.
    A missing table (SQL not run yet) is logged, never fatal."""
    if r["status"] not in ("found", "none", "unavailable"):
        return
    rest = {k: v for k, v in r.items() if k not in ("status", "known_issues", "known_praise")}
    try:
        db.table("receipts_results").upsert(
            {"query_id": query_id, "status": r["status"], "known_issues_json": r["known_issues"],
             "known_praise_json": r["known_praise"], "sources_json": rest}, on_conflict="query_id").execute()
    except Exception as e:  # noqa: BLE001
        log.warning("receipts_results not saved: %s", e)


def _market_info(product):
    """Which market a verdict was checked in, for the UI's "Checked against … retailers" line."""
    loc = locales.get(product.get("market"))
    return {"code": product.get("market") or locales.DEFAULT, "name": loc["name"], "currency": loc["currency"],
            "symbol": loc["symbol"]}


def stream(product: dict, label: str | None = None, with_receipts: bool = True):
    """Yields step events; the last one is {"stage": "done", "result": {...}}. with_receipts=False skips "What owners
    say" (used to prove it changes no score)."""
    steps = []

    def step(**s):
        if s.get("status") != "running":
            steps.append(s)
        return s

    query_id = db.table("queries").insert({"product_query": label or product["listing_id"]}).execute().data[0]["id"]

    yield step(stage="trust", status="running", message="Checking the listing, seller and market price…")
    # Photo/store forensics report progress while trust runs: forward them live as {"stage": "forensics", "type": ...}.
    # Streamed only, not saved to the chain (the evidence itself is in trust_results).
    t = yield from _live(trust.run, "forensics", product)
    db.table("trust_results").insert(
        {"query_id": query_id, "verdict": t["verdict"], "signals_json": t["signals"], "reasons": t["reasons"]}
    ).execute()
    yield step(stage="trust", status="done", verdict=t["verdict"], reasons=t["reasons"])

    tm, rx = None, None
    if t["verdict"] == "fail":  # walk away: the listing is the problem, not the product: no receipts
        final, confidence = "walk_away", None
        yield step(stage="gate", status="done", decision="stop", message="Risk detected — stopping here. Timing analysis skipped.")
    else:
        yield step(stage="gate", status="done", decision="proceed", message="Trust check passed — proceeding to timing analysis.")
        if with_receipts:  # cache-only here: never spends a search or a Groq call; missing -> offered on request
            rx_events = []  # progress, buffered while timing runs; streamed after the verdict
            pool = ThreadPoolExecutor(1)
            rx = pool.submit(receipts.run, *_receipts_args(product, t), emit=rx_events.append)
            pool.shutdown(wait=False)
        yield step(stage="timing", status="running", message="Checking demand trend, price position and maker signal…")
        # Successor radar streams the same way while timing runs: {"stage": "lifecycle", "type": ...}.
        tm = yield from _live(lambda p, s, emit: timing.run(p, s, emit, owners=rx), "lifecycle", product, t["signals"])
        db.table("timing_results").insert(
            {"query_id": query_id, "verdict": tm["verdict"], "confidence": tm["confidence"], "signals_json": tm["signals"]}
        ).execute()
        yield step(stage="timing", status="done", verdict=tm["verdict"], confidence=tm["confidence"], judged=tm["judged"],
                   reasons=tm["reasons"])
        final, confidence = VERDICT[tm["verdict"]], tm["confidence"]

    # Where to buy instead: information only, after the verdict is fixed. The narrator never sees it.
    alt = alternatives.offer(final, t["signals"]["listing"], t["signals"]["market"], product.get("store_domain"))
    result = {"query_id": query_id, "final_verdict": final, "confidence": confidence, "trust": t, "timing": tm,
              "alternative": alt}

    yield step(stage="narrator", status="running", message="Composing the verdict…")
    story = narrator.run(result)
    yield step(stage="narrator", status="done", **story)

    # The decision's signals ride along so the gauge and charts render with the verdict, not after owner receipts.
    verdict = step(stage="verdict", status="done", final_verdict=final, confidence=confidence, headline=story["headline"],
                   timing_judged=tm["judged"] if tm else None, alternative=alt, wait_plan=tm and tm["wait_plan"],
                   market=_market_info(product))
    yield {**verdict, "result": {**result, "narration": story}}
    db.table("verdicts").insert({"query_id": query_id, "final_verdict": final, "reasoning_chain_json": steps}).execute()

    # After the verdict: the evidence and narration (timing already scored the counted defects; not in reasoning_chain_json).
    owners = None
    if rx is not None:
        owners = rx.result()
        if owners["status"] == "on_request" or owners.get("retry"):
            now = time.time()
            for k in [k for k, j in RECEIPTS_ON_REQUEST.items() if now - j["at"] > ON_REQUEST_TTL]:
                del RECEIPTS_ON_REQUEST[k]
            RECEIPTS_ON_REQUEST[query_id] = {"args": _receipts_args(product, t), "at": now}
        if owners["status"] != "on_request":  # a cache miss streams only its summary: no half-run engine events
            for e in rx_events:
                yield {"stage": "receipts", **e}
        yield from _owners_events(query_id, product, owners)

    yield {"stage": "done", "result": {**result, "narration": story, "steps": steps, "receipts": owners}}


def receipts_on_request(query_id: str):
    """The live "See what owners say" run for a query whose receipts weren't cached (about 2-3 min on Groq's free
    tier). Streams {"stage": "receipts", ...} progress, then the result. Saved like the automatic run."""
    # Kept until a run finishes: a stream the user dropped (left the page, lost connection) can be retried. A run already
    # going finishes in the background and lands in the cache, so a retry is quicker.
    job = RECEIPTS_ON_REQUEST.get(query_id)
    if not job:
        yield {"stage": "receipts", "type": "error", "message": "Nothing to check for this query, or it expired."}
        return
    product, listing, amazon = job["args"]
    r = yield from _live(lambda p, l, a, emit: receipts.run(p, l, a, emit=emit, live=True), "receipts",
                         product, listing, amazon)
    if r.get("retry"):  # Groq's limit: the user can ask again later
        RECEIPTS_ON_REQUEST[query_id] = {**job, "at": time.time()}
    else:
        RECEIPTS_ON_REQUEST.pop(query_id, None)
    yield from _owners_events(query_id, product, r)


def run(product: dict, label: str | None = None, with_receipts: bool = True) -> dict:
    for event in stream(product, label, with_receipts):
        pass
    return event["result"]


if __name__ == "__main__":
    import sys

    from demo_products import DEMO_CHIPS, DEMO_PRODUCTS

    names = [a for a in sys.argv[1:] if not a.startswith("-")] or list(DEMO_CHIPS)
    for name in names:
        r = run(DEMO_PRODUCTS[name], label=name)
        n = r["narration"]
        conf = f" ({r['confidence']:.0%})" if r["confidence"] else ""
        print(f"\n{'=' * 70}\n{name}  ->  {r['final_verdict'].upper()}{conf}" + ("   [FALLBACK: " + n.get("error", "") + "]" if n.get("fallback") else ""))
        print(f"HEADLINE: {n['headline']}")
        for i, s in enumerate(n["steps"], 1):
            print(f"  Step {i} — {s['title']}: {s['text']}")
        print(f"SUMMARY: {n['summary']}")
