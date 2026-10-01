"""Gate test: a trust failure must stop the pipeline before timing runs — not run it and hide the result.

Run: uv run python test_gate.py
"""
from pathlib import Path

import orchestrator
import timing
import trust
from demo_products import DEMO_PRODUCTS
from resolve import ResolveError, resolve_screenshot
from serpapi import db

SCREENS = Path(__file__).parent.parent / "phase0" / "photo_probe" / "screens"  # synthetic ad fixtures (make_ads.py)

calls = {"timing.run": 0, "engines": []}
real_timing_run, real_trust_search, real_timing_search = timing.run, trust.search, timing.search


def tripwire(*a, **k):
    calls["timing.run"] += 1
    return real_timing_run(*a, **k)


def spy(real):
    def wrapped(engine, **params):
        calls["engines"].append(engine)
        return real(engine, **params)
    return wrapped


timing.run, trust.search, timing.search = tripwire, spy(real_trust_search), spy(real_timing_search)


def check(name, product=None):
    calls.update({"timing.run": 0, "engines": []})
    r = orchestrator.run(product or DEMO_PRODUCTS[name], label=f"gate-test:{name}")
    timing_rows = db.table("timing_results").select("query_id").eq("query_id", r["query_id"]).execute().data
    return {
        "product": name,
        "trust_verdict": r["trust"]["verdict"],
        "final_verdict": r["final_verdict"],
        "timing_agent_calls": calls["timing.run"],
        "serpapi_engines_hit": sorted(set(calls["engines"])),
        "timing_results_rows": len(timing_rows),
        "timing_in_response": r["timing"] is not None,
        "steps": [s["stage"] + (f":{s.get('decision') or s.get('verdict') or s.get('final_verdict')}") for s in r["steps"]],
    }


if __name__ == "__main__":
    import json

    # airpods-ebay fails on price; airpods-openbox on seller flags + photo evidence (stock render on a used listing).
    for name in ("airpods-ebay", "airpods-openbox"):
        fail = check(name)
        print(f"GATE FAILURE CASE ({name}):\n" + json.dumps(fail, indent=2))
        assert fail["trust_verdict"] == "fail"
        assert fail["final_verdict"] == "walk_away"
        assert fail["timing_agent_calls"] == 0, "timing agent was invoked on a trust failure"
        assert not {"google_trends", "google_finance"} & set(fail["serpapi_engines_hit"]), "timing data was fetched"
        assert fail["timing_results_rows"] == 0 and not fail["timing_in_response"]
        assert "timing" not in [s.split(":")[0] for s in fail["steps"]]

    # Screenshot input: a Sony XM5 ad with $59 typed as the ad's price must stop at trust too.
    _, shot = resolve_screenshot((SCREENS / "ad_sony_xm5_59.png").read_bytes(), 59.0)
    fail = check("screenshot:ad_sony_xm5_59", shot)
    print("GATE FAILURE CASE (screenshot):\n" + json.dumps(fail, indent=2))
    assert fail["trust_verdict"] == "fail" and fail["final_verdict"] == "walk_away"
    assert fail["timing_agent_calls"] == 0 and fail["timing_results_rows"] == 0 and not fail["timing_in_response"]
    assert not {"google_trends", "google_finance"} & set(fail["serpapi_engines_hit"]), "timing data was fetched"

    # A screenshot too general to price-check stops before trust with a clean message: no verdict at all.
    try:
        resolve_screenshot((SCREENS / "ad_airpods_49.png").read_bytes(), 49.0)
        raise AssertionError("'AirPods Pro' at $49 should not have produced a product to judge")
    except ResolveError as e:
        print(f"\nUNPRICEABLE SCREENSHOT (clean stop, no verdict): {e}")

    control = check("sony-xm5")
    print("\nCONTROL (passing product, proves the tripwire fires):\n" + json.dumps(control, indent=2))
    assert control["timing_agent_calls"] == 1 and control["timing_results_rows"] == 1
    assert {"google_trends", "google_finance"} <= set(control["serpapi_engines_hit"])

    print("\nGATE TEST PASSED")
