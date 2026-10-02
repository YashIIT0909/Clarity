"""Runs the pipeline /analyze/stream runs (resolve -> orchestrator.stream), in-process, and logs every event with the
time it was emitted. Same functions, same Supabase rows as a web run: no scoring here (trap #7). Never raises."""
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import orchestrator
from resolve import ResolveError, resolve
from serpapi import db

TIMEOUT = 45  # s. Hosts on the TS MCP SDK give up at 60 s (measured in the Prompt 1 spike).
SETTLE = 10  # s to wait for a just-returned check to finish saving its owner receipts (it drains after the verdict)
# query_id -> the live "What owners say" run in progress. ponytail: in-memory, single process, like
# orchestrator.RECEIPTS_ON_REQUEST; a second click joins the running one instead of paying for another.
LIVE: dict[str, dict] = {}
MAX_INPUT = 2000  # chars; a URL or a product description, never a document

# Drill switch for the agent behaviour checks: every MCP check fails as if the pipeline were down. Off unless exactly "1";
# loud when on (a warning at startup and on every forced call). Touches only MCP calls, never the web app's routes.
FORCE_FAIL = os.getenv("CLARITY_MCP_FORCE_FAIL") == "1"
logger = logging.getLogger("uvicorn.error")  # shows in uvicorn's console next to its own startup lines
if FORCE_FAIL:
    logger.warning("CLARITY_MCP_FORCE_FAIL=1: every clarity_check_listing call will FAIL (drill). Unset it and restart.")


def run(listing, source="mcp") -> dict:
    """{"status": "done", "result": <the orchestrator's result + narration>, ...} or {"status": "could_not_check",
    "reason": ...}. Both carry "events" ([{"t_ms", **event}] in emitted order, up to the verdict) and "started_at".
    Returns at the verdict; the stream keeps running in its thread (owner receipts, the verdicts row), as on the web.
    source prefixes the history label (queries.product_query), e.g. "mcp:sony-xm5" or "mcp-test:sony-xm5"."""
    started_at, t0 = datetime.now(timezone.utc).isoformat(), time.monotonic()
    events, out, ready = [], {}, threading.Event()

    def log(e):
        events.append({"t_ms": round((time.monotonic() - t0) * 1000), **e})

    def could_not_check(reason, seen):
        return {"status": "could_not_check", "reason": reason, "events": seen, "started_at": started_at}

    if not isinstance(listing, str) or not listing.strip():
        return could_not_check("no listing was given", [])
    if len(listing) > MAX_INPUT:
        return could_not_check(f"the input is longer than {MAX_INPUT} characters", [])

    def work():
        try:
            log({"stage": "resolve", "status": "running"})
            if FORCE_FAIL:
                logger.warning("CLARITY_MCP_FORCE_FAIL=1: forcing this check to fail (drill)")
                raise ConnectionError("simulated outage")
            label, product = resolve(listing)
            # Same fields main._stream sends for a link.
            log({"stage": "resolve", "status": "done", "label": product.get("label", label),
                 "marketplace": product["marketplace"], "listing_id": product["listing_id"]})
            stream = orchestrator.stream(product, label=f"{source}:{label}")
            for e in stream:
                if ready.is_set():
                    continue  # after the verdict: drained so the rest of the run is saved, not returned
                log(e)
                if e["stage"] == "verdict":
                    # The stream is paused at its verdict `yield`; its `result` and `story` are exactly what its final
                    # "done" event will carry (minus receipts). Read, never modified.
                    loc = stream.gi_frame.f_locals
                    out["result"], out["events"] = {**loc["result"], "narration": loc["story"]}, list(events)
                    ready.set()
        except ResolveError as e:
            out.setdefault("error", str(e))
        except Exception as e:  # noqa: BLE001 — a failed check is reported, never raised into the tool layer
            out.setdefault("error", f"the check failed ({type(e).__name__}: {str(e)[:200]})")
        ready.set()

    # ponytail: a timed-out run keeps going in its thread (and finishes its Supabase writes); Python can't kill it.
    threading.Thread(target=work, daemon=True).start()
    ready.wait(TIMEOUT)
    if "result" in out:
        return {"status": "done", "result": out["result"], "events": out["events"], "started_at": started_at}
    if "error" in out:
        return could_not_check(out["error"], list(events))
    return could_not_check(f"the check didn't finish within {TIMEOUT} s", list(events))


def receipts(query_id, live=False) -> dict:
    """"What owners say" for a finished check: {"status", "events": [receipts / owners events, as the web gets them]}.
    The automatic run's result is read from receipts_results (saved by orchestrator, cache-only: 0 searches, 0 Groq).
    A product whose receipts weren't cached is "on_request"; live=True then runs orchestrator.receipts_on_request,
    the code path of GET /receipts/stream/{query_id} (about 2-3 min, real searches and Groq). The tool waits up to
    TIMEOUT; if the run isn't done the result is "running" and the run carries on, saving its result for the next ask."""
    try:
        query_id = str(uuid.UUID(str(query_id)))
    except ValueError:
        return {"status": "error", "message": "not a Clarity check id", "events": []}
    deadline = time.monotonic() + SETTLE
    while True:
        row = (db.table("receipts_results").select("status, known_issues_json, known_praise_json, sources_json")
               .eq("query_id", query_id).limit(1).execute().data)
        if row:
            return _saved(query_id, row[0])
        if query_id in orchestrator.RECEIPTS_ON_REQUEST or query_id in LIVE:
            break
        if time.monotonic() > deadline:
            return {"status": "error", "message": "no owner reviews for this check (expired, or never run)", "events": []}
        time.sleep(0.5)
    if not live and query_id not in LIVE:
        return {"status": "on_request", "events": [{"stage": "receipts", "type": "summary", "query_id": query_id,
                "status": "on_request", "known_issues": [], "known_praise": [], "forum_reported": [], "mentioned_once": []}]}
    job = LIVE.get(query_id)
    if job is None:
        job = LIVE[query_id] = {"events": [], "done": threading.Event()}

        def work():
            try:
                for e in orchestrator.receipts_on_request(query_id):
                    job["events"].append(e)
            except Exception as e:  # noqa: BLE001 — reported to the view, like main.receipts_stream
                job["events"].append({"stage": "receipts", "type": "error", "message": f"receipts failed: {str(e)[:200]}"})
            finally:
                job["done"].set()
                LIVE.pop(query_id, None)

        threading.Thread(target=work, daemon=True).start()
    if job["done"].wait(TIMEOUT):
        return {"status": "done", "events": job["events"]}
    return {"status": "running", "message": "still checking review videos and forums; ask again in a minute",
            "events": []}


def _saved(query_id, row):
    """A receipts_results row -> the "summary" and "owners" events the automatic run streamed."""
    rest = dict(row["sources_json"] or {})
    story = rest.pop("narration", None)
    summary = {"stage": "receipts", "type": "summary", "query_id": query_id, "status": row["status"],
               "known_issues": row["known_issues_json"], "known_praise": row["known_praise_json"], **rest}
    return {"status": row["status"], "events": [summary] + ([{"stage": "owners", "status": "done", **story}] if story else [])}
