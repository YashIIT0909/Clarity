"""Engine ablation: proves what each SerpApi engine contributes across the 10 pinned demo fixtures.
Reruns the pipeline offline (TTL 0, network blocked) with each engine or engine mode treated as UNAVAILABLE
(zero penalty / neutral signal, identical to an engine timeout).

Run:
    uv run python test_ablation.py
"""
import os
import sys
from datetime import timedelta
from pathlib import Path

import httpx

import orchestrator
import photo_forensics
import receipts
import resolve
import serpapi
import store_reputation
import timing
import trust
from demo_products import DEMO_CHIPS, DEMO_SCREENSHOTS
from resolve import ResolveError, resolve_screenshot

# Block external calls: 0 searches, 0 Groq tokens, fully offline replay
serpapi.TTL = timedelta(0)
serpapi.httpx.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("SerpApi blocked: offline replay only"))
httpx.post = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("Groq blocked: offline replay only"))

real_cached = serpapi.cached
real_search = serpapi.search

ALL_FIXTURES = list(DEMO_CHIPS) + list(DEMO_SCREENSHOTS)
AMAZON_FIXTURES = ["sony-xm5", "honeywell-heater", "sony-xm5-in", "switch2"]
EBAY_FIXTURES = ["airpods-ebay", "airpods-openbox", "honeywell-ebay"]
SCREENSHOT_FIXTURES = ["screenshot-sony-59", "screenshot-sony-rs4999", "screenshot-sony-rs29990"]
TIMING_FIXTURES = ["sony-xm5", "honeywell-heater", "honeywell-ebay", "sony-xm5-in", "switch2", "screenshot-sony-rs29990"]

ENGINES = [
    ("amazon_product", "Amazon ASIN lookup",
     lambda e, p: e == "amazon_product",
     AMAZON_FIXTURES),
    ("ebay_product", "eBay item ID lookup",
     lambda e, p: e == "ebay_product",
     EBAY_FIXTURES),
    ("amazon", "Amazon catalog search",
     lambda e, p: e == "amazon",
     []),
    ("google_lens (products)", "Ad screenshot visual match",
     lambda e, p: e == "google_lens" and p.get("type") in ("products", "all", None),
     SCREENSHOT_FIXTURES),
    ("google_shopping", "New retail market pricing",
     lambda e, p: e == "google_shopping",
     AMAZON_FIXTURES + ["honeywell-ebay"] + SCREENSHOT_FIXTURES),
    ("google_immersive_product", "Authorized store directory",
     lambda e, p: e == "google_immersive_product",
     ["honeywell-heater", "honeywell-ebay", "sony-xm5-in"] + SCREENSHOT_FIXTURES),
    ("ebay (search)", "Secondhand price distribution",
     lambda e, p: e == "ebay",
     EBAY_FIXTURES),
    ("google_lens (exact_matches)", "Photo forensics exact matches",
     lambda e, p: e == "google_lens" and p.get("type") == "exact_matches",
     ALL_FIXTURES),
    ("bing_reverse_image", "Secondary reverse image search",
     lambda e, p: e == "bing_reverse_image",
     ALL_FIXTURES),
    ("google (reviews/scam)", "Shop reputation search (reviews & scams)",
     lambda e, p: e == "google",
     SCREENSHOT_FIXTURES),
    ("google_forums (store rep)", "Shop discussions on forums",
     lambda e, p: e == "google_forums" and not any(w in str(p.get("q", "")) for w in ("problems", "issues")),
     SCREENSHOT_FIXTURES),
    ("google_about_this_result", "Knowledge Graph domain authority",
     lambda e, p: e == "google_about_this_result",
     SCREENSHOT_FIXTURES),
    ("google_ads_transparency_center", "Merchant ad verification status",
     lambda e, p: e == "google_ads_transparency_center",
     SCREENSHOT_FIXTURES),
    ("google_trends (TIMESERIES)", "5-year demand momentum & seasonality",
     lambda e, p: e == "google_trends" and p.get("data_type") == "TIMESERIES",
     TIMING_FIXTURES),
    ("google_finance", "Maker 6-month stock trajectory",
     lambda e, p: e == "google_finance",
     TIMING_FIXTURES),
    ("google_trends (RELATED_QUERIES)", "Successor radar rising queries",
     lambda e, p: e == "google_trends" and p.get("data_type") == "RELATED_QUERIES",
     ["sony-xm5", "sony-xm5-in", "switch2", "screenshot-sony-rs29990"]),
    ("google_autocomplete", "Successor candidate query suggestions",
     lambda e, p: e == "google_autocomplete",
     ["sony-xm5", "sony-xm5-in", "switch2", "screenshot-sony-rs29990"]),
    ("google_news", "Successor release status headlines",
     lambda e, p: e == "google_news",
     ["sony-xm5", "sony-xm5-in", "switch2", "screenshot-sony-rs29990"]),
    ("youtube", "Owner reviews YouTube search",
     lambda e, p: e == "youtube",
     TIMING_FIXTURES),
    ("youtube_video_transcript", "Timestamped review transcripts",
     lambda e, p: e == "youtube_video_transcript",
     TIMING_FIXTURES),
    ("google_forums (receipts)", "Owner problem threads on forums",
     lambda e, p: e == "google_forums" and any(w in str(p.get("q", "")) for w in ("problems", "issues")),
     TIMING_FIXTURES),
]


def run_fixture(key, ablated_predicate=None):
    def mock_cached(engine, cache_key=None, **params):
        p = cache_key or params
        if ablated_predicate and ablated_predicate(engine, p):
            return None
        return real_cached(engine, cache_key, **params)

    def mock_search(engine, cache_key=None, **params):
        p = cache_key or params
        if ablated_predicate and ablated_predicate(engine, p):
            if engine in ("google_lens", "bing_reverse_image"):
                if p.get("type") in ("products", "all"):
                    return {"visual_matches": []}
                raise TimeoutError(f"{engine} timed out")
            if engine in ("google", "google_about_this_result", "google_ads_transparency_center"):
                raise TimeoutError(f"{engine} timed out")
            if engine == "google_forums":
                raise TimeoutError("google_forums timed out")
            if engine == "google_trends":
                if p.get("data_type") == "TIMESERIES":
                    return {"interest_over_time": {"timeline_data": []}}
                raise TimeoutError("google_trends timed out")
            if engine == "google_finance":
                return {"graph": []}
            if engine in ("google_autocomplete", "google_news"):
                raise TimeoutError(f"{engine} timed out")
            if engine == "google_shopping":
                return {"shopping_results": []}
            if engine == "google_immersive_product":
                return {"product_results": {"stores": []}, "_fetched_at": None}
            if engine == "ebay":
                return {"organic_results": []}
            if engine in ("amazon_product", "ebay_product", "amazon"):
                raise TimeoutError(f"{engine} timed out")
            raise TimeoutError(f"{engine} timed out")

        # Check cache
        if hit := real_cached(engine, cache_key, **params):
            return hit

        # Fallback only on cache miss (e.g. walk-away listing that blew past gate into timing):
        if engine == "google_trends":
            if p.get("data_type") == "TIMESERIES":
                return {"interest_over_time": {"timeline_data": []}}
            return {"related_queries": {"rising": []}}
        if engine == "google_finance":
            return {"graph": []}
        if engine in ("google_autocomplete", "google_news"):
            return {}
        return real_search(engine, cache_key, **params)

    serpapi.cached = orchestrator.cached = mock_cached
    serpapi.search = trust.search = timing.search = resolve.search = mock_search

    try:
        if key in DEMO_CHIPS:
            prod = DEMO_CHIPS[key]
        else:
            s = DEMO_SCREENSHOTS[key]
            raw, prod = resolve_screenshot((Path(__file__).parent / s["file"]).read_bytes(), s["price"], s.get("market"))

        events = list(orchestrator.stream(prod, label=f"ablation:{key}", with_receipts=True))
        res = events[-1]["result"]
        v = res.get("final_verdict")
        conf = res.get("confidence")
        tr_sc = res["trust"]["signals"]["score"] if res.get("trust") and res["trust"].get("signals") else None
        tm_sc = res["timing"]["signals"]["score"] if res.get("timing") and res["timing"].get("signals") else None
        verdict_ev = next((e for e in events if e.get("stage") == "verdict"), None)
        alt = verdict_ev.get("alternative") if verdict_ev else None
        return {"verdict": v, "confidence": conf, "trust_score": tr_sc, "timing_score": tm_sc, "alt": alt, "error": None}
    except Exception as exc:
        return {"verdict": "could_not_check", "confidence": None, "trust_score": None, "timing_score": None, "alt": None, "error": str(exc)}
    finally:
        serpapi.cached = orchestrator.cached = real_cached
        serpapi.search = trust.search = timing.search = resolve.search = real_search


def main():
    # 1. Baseline
    baseline = {k: run_fixture(k) for k in ALL_FIXTURES}

    print("=" * 110)
    print("SERPAPI ENGINE ABLATION MATRIX (10 PINNED DEMO FIXTURES, OFFLINE)")
    print("=" * 110)
    print(f"{'Engine removed':32} | {'Verdicts changed':20} | {'Largest score shift':26} | {'Example'}")
    print("-" * 110)

    no_change_engines = []

    for eng_name, desc, pred, candidate_keys in ENGINES:
        changed_verdicts = []
        max_shift = 0.0
        max_shift_desc = "None"
        example_desc = "No change"

        for k in candidate_keys:
            b = baseline[k]
            abl = run_fixture(k, pred)

            if abl["verdict"] != b["verdict"]:
                changed_verdicts.append(f"{k} ({b['verdict']} -> {abl['verdict']})")

            # Score shift check
            if b["trust_score"] is not None and abl["trust_score"] is not None:
                shift = abs(abl["trust_score"] - b["trust_score"])
                if shift > max_shift:
                    max_shift = shift
                    max_shift_desc = f"Trust {b['trust_score']} -> {abl['trust_score']} (Δ{shift})"
                    example_desc = f"{k}: trust {b['trust_score']} -> {abl['trust_score']}"

            if b["timing_score"] is not None and abl["timing_score"] is not None:
                shift = round(abs(abl["timing_score"] - b["timing_score"]), 3)
                if shift > max_shift:
                    max_shift = shift
                    max_shift_desc = f"Timing {b['timing_score']:+.3f} -> {abl['timing_score']:+.3f} (Δ{shift:.3f})"
                    example_desc = f"{k}: timing {b['timing_score']:+.3f} -> {abl['timing_score']:+.3f}"

            if b["alt"] and not abl["alt"] and not changed_verdicts:
                if max_shift_desc == "None":
                    max_shift_desc = "Alt retailer lost"
                    example_desc = f"{k}: lost {b['alt']['store']} alternative"

        if changed_verdicts:
            v_str = f"{len(changed_verdicts)} changed"
            ex = changed_verdicts[0]
            print(f"{eng_name:32} | {v_str:20} | {max_shift_desc:26} | {ex}")
        elif max_shift > 0 or max_shift_desc != "None":
            print(f"{eng_name:32} | {'0 (scores shifted)':20} | {max_shift_desc:26} | {example_desc}")
        else:
            no_change_engines.append(eng_name)

    print("-" * 110)
    print(f"Engines with no demo changes: {', '.join(no_change_engines)}")
    print("=" * 110)
    print("ABLATION TEST FINISHED SUCCESSFULLY.")


if __name__ == "__main__":
    main()
