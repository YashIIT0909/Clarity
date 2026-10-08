"""ClarityResult: what the agent sees (structuredContent) and what only the view gets (the event log, in _meta).
A pure mapping of the pipeline's own result: nothing is scored, guessed or defaulted to a good value here. A part that
didn't run or wasn't available is None, and `unavailable` says why."""
from datetime import datetime

SAFE = {"buy_now", "buy_timing_not_judged"}
SELLER_FACTS = ("seller", "seller_is_platform", "seller_sales", "seller_feedback", "seller_joined", "returns_accepted",
                "rating", "reviews", "condition")
CLAUSE_ENDS = (" (", ", ", " — ", "; ")
MAX_PHOTO_EVIDENCE = 4  # pixel-verified matches per photo signal, in the view's log
MAX_CANDIDATE_EVIDENCE = 3  # per successor candidate


def _day(iso):
    return iso and datetime.fromisoformat(iso).strftime("%-d %b %Y")


def summary(verdict, confidence=None, reason=None, as_of=None):
    """The one line the agent reads: the text block and structuredContent["summary"] are this exact string."""
    if verdict == "could_not_check":
        return f"Clarity couldn't check this listing ({reason}). This is not a pass; don't treat the listing as verified."
    if verdict in ("buy_now", "wait"):  # always judged (not judged is its own verdict), so a confidence exists
        label = f"{'BUY NOW' if verdict == 'buy_now' else 'WAIT'} ({confidence:.0%} confidence)."
    else:
        label = {"walk_away": "WALK AWAY — do not buy this listing.",
                 "buy_timing_not_judged": "SAFE TO BUY — timing not judged."}[verdict]
    line = f"Clarity: {label}"
    if reason:
        line += f" {reason[0].upper()}{reason[1:].rstrip('.')}."
    return line + (f" Data as of {_day(as_of)}." if as_of else "")


def _one_clause(text):
    return text[:min((i for i in (text.find(c) for c in CLAUSE_ENDS) if i > 0), default=len(text))]


def _why(verdict, sig, tm):
    """The summary's reason, from the pipeline's own fields (never the narrator's prose). walk_away: the heaviest
    flag (ties keep the pipeline's order). Otherwise: the price position if it was judged, else the judged component
    that pushed hardest toward the verdict; its first evidence fact (current data first, by the pipeline's order)."""
    if verdict == "walk_away":
        flags = sorted(sig["penalties"], key=lambda p: -p["weight"])
        return flags and _one_clause(flags[0]["reason"])
    judged = {n: c for n, c in (tm["signals"]["components"] if tm else {}).items() if c["judged"]}
    if not judged:
        return None
    sign = -1 if verdict == "wait" else 1
    name = "price" if "price" in judged else max(judged, key=lambda n: sign * judged[n]["weight"] * judged[n]["score"])
    return next((e["fact"] for e in tm["evidence"] if e["signal"] == name), None)


def view_log(events):
    """The event log trimmed to what the view shows: photo look-alikes reduced to their count (only the pixel-verified
    matches inside forensics_signals are shown, capped), successor evidence capped."""
    out, counted = [], False
    for e in events:
        t = e.get("type")
        if t == "forensics_matches":
            if not counted:
                out.append({k: e[k] for k in ("t_ms", "stage", "type", "total_display")})
                counted = True
            continue
        if t == "forensics_signals":
            e = {**e, "signals": [{**s, "evidence": s["evidence"][:MAX_PHOTO_EVIDENCE]} for s in e["signals"]]}
        if t == "candidates":
            e = {**e, "candidates": [{**c, "evidence": c["evidence"][:MAX_CANDIDATE_EVIDENCE]} for c in e["candidates"]]}
        out.append(e)
    return out


def could_not_check(run):
    timing_ran = any(e["stage"] == "timing" and e.get("status") == "done" for e in run["events"])
    s = {"verdict": "could_not_check", "safe_to_buy": False, "summary": summary("could_not_check", reason=run["reason"]),
         "reason": run["reason"], "timing_ran": timing_ran, "trust": None, "timing": None, "reasons": [], "alternative": None,
         "evidence": None, "data_as_of": None, "query_id": None, "checked_at": run["started_at"],
         "unavailable": {"everything": run["reason"]}}
    return s, {"events": view_log(run["events"])}


def build(run):
    """runner.run(...) output -> (structuredContent, _meta)."""
    if run["status"] != "done":
        return could_not_check(run)
    r = run["result"]
    tr, tm, story = r["trust"], r["timing"], r["narration"]
    sig, unavailable = tr["signals"], {}

    verdict = r["final_verdict"]
    if verdict == "buy_now" and tm and not tm["judged"]:
        verdict = "buy_timing_not_judged"

    listing, market = sig["listing"], sig["market"]
    as_of = {"listing": listing.get("observed_at"), "retail_stores": market.get("stores_observed_at")}
    if not as_of["retail_stores"]:
        unavailable["data_as_of.retail_stores"] = "no retailer store list was used for this market price"

    price = {"listing_price": listing.get("price"), "median": market.get("median") and round(market["median"], 2),
             "sample_size": market.get("n_prices"),
             "condition_band": market.get("basis_text"), "position_vs_median": sig.get("price_ratio")}
    if market.get("median") is None:
        unavailable["evidence.price.median"] = next((w["reason"] for w in sig.get("waived", [])
                                                     if w["check"] == "market_unknown"), "no market median")

    photo = sig.get("photo") or {}
    forensics = photo.get("forensics") or {}
    photo_ev = {"engines_checked": forensics.get("engines_used"), "engines_unavailable": forensics.get("engines_unavailable"),
                "fired": [{"signal": p["signal"], "reason": p["reason"]} for p in photo.get("signals", []) if p["status"] == "fired"],
                "skipped": [p["signal"] for p in photo.get("signals", []) if p["status"] == "skipped"]}

    timing = successor = None
    if tm:
        comps = tm["signals"]["components"]
        timing = {"confidence": tm["confidence"], "judged": tm["judged"],
                  "components": [{"name": n, "weight": c["weight"], "score": c["score"], "judged": c["judged"]}
                                 for n, c in comps.items()]}
        lc = comps.get("lifecycle") or {}
        successor = {"status": lc.get("status"),
                     "newer_model": next((c["name"] for c in lc.get("candidates") or []), None)}
        if lc.get("status") == "unavailable":
            unavailable["evidence.successor"] = lc.get("why") or "successor check unavailable"
    else:
        unavailable["timing"] = unavailable["evidence.successor"] = "not run: the listing failed the trust check"

    reasons = [f"{s['title']}: {s['text']}" for s in story["steps"]][:5]  # the narrator's own wording
    alt = r.get("alternative")
    s = {
        "verdict": verdict,
        "safe_to_buy": verdict in SAFE,
        "summary": summary(verdict, r["confidence"], _why(verdict, sig, tm), as_of["listing"]),
        "timing_ran": tm is not None,
        "trust": {"score": sig["score"], "pass_line": sig["pass_score"],
                  "flags": [{"name": p["check"], "weight": p["weight"], "reason": p["reason"]} for p in sig["penalties"]]},
        "timing": timing,
        "reasons": reasons,
        # Where to buy instead (alternatives.py): walk_away, or wait when it's cheaper. None when no trusted store
        # qualifies; Clarity never guesses one.
        "alternative": alt and {k: alt[k] for k in ("store", "price", "link", "saving", "text")},
        "evidence": {"price": price, "photo": photo_ev,
                     "seller": {k: listing[k] for k in SELLER_FACTS if listing.get(k) is not None},
                     "successor": successor},
        "data_as_of": as_of,
        "query_id": r["query_id"],
        "checked_at": run["started_at"],
        "unavailable": unavailable,
    }
    return s, {"events": view_log(run["events"])}
