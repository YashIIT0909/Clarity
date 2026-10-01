"""Weight 0: "What owners say" (receipts) must not change any trust or timing score or verdict. Every demo chip runs with
receipts off, with receipts on (as cached today), and with receipts forced to a loud "found" result (many known issues);
all three must agree on every score and verdict. Walk-aways must not run receipts at all.

0 SerpApi searches (demo chips are pinned; live SerpApi is blocked) and 0 Groq calls (narrator on its rule-based
fallback; receipts run cache-only). Writes to Supabase like test_gate.py.
  .venv/bin/python test_receipts_weight0.py
"""
from pathlib import Path

import narrator
import orchestrator
import receipts
import serpapi
from demo_products import DEMO_CHIPS, DEMO_SCREENSHOTS
from resolve import resolve_screenshot

serpapi.httpx.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("SerpApi blocked in this test"))
narrator.httpx.post = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("no LLM in this test"))
receipts.httpx.post = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("no LLM in this test"))

LOUD = {"status": "found", "known_issues": [{"kind": "issue", "label": f"issue {i}", "source_count": 5, "sources": []}
                                           for i in range(9)],
        "known_praise": [], "forum_reported": [], "mentioned_once": [], "forum_only": [], "groq_tokens": 0, "seconds": 0}


def scores(r):
    """Every score and verdict a run produces (receipts and narration excluded)."""
    t, tm = r["trust"], r["timing"]
    return {"final": r["final_verdict"], "confidence": r["confidence"],
            "trust": (t["verdict"], t["signals"]["score"], sorted(p["check"] for p in t["signals"]["penalties"])),
            "timing": tm and (tm["verdict"], tm["confidence"], tm["judged"], tm["signals"]["score"],
                              {k: c["score"] for k, c in tm["signals"]["components"].items()})}


def products():
    for key, p in DEMO_CHIPS.items():
        yield key, p
    for key, s in DEMO_SCREENSHOTS.items():
        yield key, resolve_screenshot((Path(__file__).parent / s["file"]).read_bytes(), s["price"])[1]


if __name__ == "__main__":
    real_run = receipts.run
    for key, p in products():
        off = orchestrator.run(p, label=f"weight0-test:{key}:off", with_receipts=False)
        on = orchestrator.run(p, label=f"weight0-test:{key}:on")
        receipts.run = lambda *a, **k: LOUD
        loud = orchestrator.run(p, label=f"weight0-test:{key}:loud")
        receipts.run = real_run
        assert scores(off) == scores(on) == scores(loud), (key, scores(off), scores(on), scores(loud))
        assert off["receipts"] is None
        if off["trust"]["verdict"] == "fail":  # walk-away: receipts never run
            assert on["receipts"] is None and loud["receipts"] is None, key
        else:
            assert on["receipts"]["status"] in ("found", "none", "unavailable", "on_request") and loud["receipts"] is LOUD
        print(f"{key:22} {scores(off)['final']:9} confidence {scores(off)['confidence']} | receipts on: "
              f"{on['receipts'] and on['receipts']['status']} | identical with receipts off / on / loud")
    print("RECEIPTS WEIGHT-0 TEST PASSED (0 SerpApi searches, 0 Groq calls)")
