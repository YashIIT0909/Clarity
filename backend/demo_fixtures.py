"""Demo fixtures: pin demo products' SerpApi responses in serpapi_cache so they never expire (listings end before
judging; the cache TTL is 72h). Costs 0 searches: it only pins what is already cached, or imports a raw response a
phase0 probe fetched outside the cache. Re-runnable. Run: .venv/bin/python demo_fixtures.py"""
import json
from concurrent.futures import Future
from datetime import datetime, timedelta
from pathlib import Path

import httpx

import photo_forensics
import serpapi
from demo_products import DEMO_CHIPS, DEMO_PRODUCTS, DEMO_SCREENSHOTS

PROBE = Path(__file__).parent.parent / "phase0" / "photo_probe" / "out"
# Probe files are named <demo key>_<engine>.json, except the Prompt 2 candidate, named by eBay item id.
PROBE_PREFIX = {"airpods-openbox": "287595183543"}
PROBE_ENGINE = {"google_lens": "lens_exact_matches", "bing_reverse_image": "bing_reverse_image"}
# Listing calls to pin, per demo key. ponytail: by hand; market/store calls get added once those are final.
LISTING_CALLS = {"airpods-openbox": [("ebay_product", {"product_id": "287595183543"})]}
IMAGES_TO_SAVE = {"airpods-openbox": PROBE / "287595183543.jpg"}
# Successor radar (lifecycle) probe, 2026-09-30: same US-locale calls timing makes. The heater has no model identifier
# ("space heater"), so it makes no lifecycle calls.
LIFECYCLE_PROBE = Path(__file__).parent.parent / "phase0" / "successor_probe" / "out"
GATE_SCREENS = Path(__file__).parent.parent / "phase0" / "photo_probe" / "screens"  # test_gate.py's synthetic ads
LIFECYCLE_FILES = {"trends": "trends_rising", "autocomplete": "google_autocomplete", "news": "google_news"}


def _probe_time(d):
    """search_metadata.created_at ("2026-09-29 11:12:13 UTC") = when SerpApi actually ran the search."""
    return datetime.strptime(d["search_metadata"]["created_at"], "%Y-%m-%d %H:%M:%S UTC").isoformat() + "Z"


def fixtures():
    """(demo key, engine, cache_key, request params, probe file | None) for every pinned call."""
    for key, p in DEMO_CHIPS.items():
        for engine, params in LISTING_CALLS.get(key, []):
            yield key, engine, None, params, None
        if p.get("image"):
            ident = photo_forensics.identity(url=p["image"])
            for engine, c in photo_forensics.ENGINES.items():
                f = PROBE / f"{PROBE_PREFIX.get(key, key)}_{PROBE_ENGINE[engine]}.json"
                yield key, engine, photo_forensics.cache_key(engine, ident), {c["by_url"]: p["image"], **c["fixed"]}, f
        if key in ("sony-xm5", "switch2"):
            import lifecycle
            for src, (engine, params) in lifecycle.queries(p["trend_query"]).items():
                yield key, engine, None, params, LIFECYCLE_PROBE / f"{key}_{LIFECYCLE_FILES[src]}.json"


def pin_all():
    for key, engine, ck, params, probe_file in fixtures():
        if probe_file:
            raw = json.loads(probe_file.read_text())
            sent = {k: v for k, v in raw["search_parameters"].items() if k != "engine"}
            # SerpApi also echoes defaults it filled in (Trends: tz), so the request must be a subset of what was sent
            assert sent.items() >= params.items(), f"{probe_file.name} was run on {sent}, not the demo call {params}"
            serpapi.put(engine, raw, _probe_time(raw), ck, **params)
        else:
            serpapi.pin(engine, **params)
        print(f"pinned {key}: {engine} {ck or params}")
    for key, path in IMAGES_TO_SAVE.items():
        if not path.exists():
            path.write_bytes(httpx.get(DEMO_PRODUCTS[key]["image"], timeout=30).raise_for_status().content)
            print(f"saved {path}")


def pin_demo_runs():
    """Pin every serpapi_cache row the demo set reads: run each chip's agents (trust, then timing if it passes), its
    verdict narration, and, when trust passes, its owner receipts and "What owners say", plus each demo screenshot,
    with SerpApi AND Groq blocked; record the keys they look up, pin those rows. Pre-warm first (run the chips once):
    a cache miss here fails loudly instead of spending a search or a Groq token. No trust_results rows."""
    import httpx as httpx_module

    import narrator
    import orchestrator
    import receipts
    import timing
    import trust
    from resolve import resolve_screenshot

    keys, real_cached, real_get, real_post = set(), serpapi.cached, serpapi.httpx.get, httpx_module.post

    def record(engine, cache_key=None, **params):
        keys.add((engine, serpapi._hash(engine, cache_key or params)))
        return real_cached(engine, cache_key, **params)

    serpapi.cached = orchestrator.cached = record  # orchestrator imported `cached` by name
    serpapi.httpx.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("not pre-warmed: run the demo chips first"))
    httpx_module.post = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("Groq blocked: not pre-warmed"))
    try:
        runs = [(k, p) for k, p in DEMO_CHIPS.items()]
        for k, shot in DEMO_SCREENSHOTS.items():
            runs.append((k, resolve_screenshot((Path(__file__).parent / shot["file"]).read_bytes(), shot["price"])[1]))
        for key, product in runs:
            t = trust.run(product)
            tm = r = None
            if t["verdict"] == "pass":  # receipts first: timing scores them, as in the orchestrator
                r = receipts.run(*orchestrator._receipts_args(product, t))
                assert r["status"] in ("found", "none", "unavailable") and not r.get("retry"), f"{key}: receipts {r['status']}"
                done = Future()
                done.set_result(r)
                tm = timing.run(product, trust_signals=t["signals"], owners=done)
            final = orchestrator.VERDICT[tm["verdict"]] if tm else "walk_away"
            story = narrator.run({"query_id": None, "final_verdict": final, "confidence": tm and tm["confidence"],
                                  "trust": t, "timing": tm})
            assert not story.get("fallback"), f"{key}: verdict narration not cached ({story.get('error')})"
            owners = None
            if tm:
                owners = narrator.owners(r, r.get("name") or product.get("label"))
                assert r["status"] != "found" or not owners.get("fallback"), f"{key}: owners narration not cached"
            print(f"read {key}: trust {t['verdict']}, {final}, receipts {owners and r['status']}")
        # test_gate's generation-less screenshot: its Lens, market and photo-check lookups must never go live again (4 searches)
        from resolve import ResolveError
        try:  # resolved -> test_gate also runs trust (photo forensics) and narrates the walk-away: pin those too
            vague = resolve_screenshot((GATE_SCREENS / "ad_airpods_49.png").read_bytes(), 49.0)[1]
            t = trust.run(vague)
            story = narrator.run({"query_id": None, "final_verdict": "walk_away", "confidence": None, "trust": t, "timing": None})
            assert t["verdict"] == "fail" and not story.get("fallback"), "test_gate ad_airpods_49: not a cached walk-away"
            print("read test_gate ad_airpods_49: resolved, trust fail, walk_away")
        except ResolveError:
            print("read test_gate ad_airpods_49: clean stop (too general to price-check)")
    finally:
        serpapi.cached, serpapi.httpx.get, httpx_module.post = real_cached, real_get, real_post
        orchestrator.cached = real_cached
    for engine, h in sorted(keys):
        serpapi.db.table("serpapi_cache").update({"pinned": True}).eq("engine", engine).eq("query_params_hash", h).execute()
    print(f"pinned {len(keys)} rows the demo set reads")
    return keys


def check():
    """With TTL forced to 0 and the network blocked: every fixture still loads; an unpinned row does not."""
    real_ttl, real_get = serpapi.TTL, serpapi.httpx.get
    serpapi.TTL = timedelta(0)
    serpapi.httpx.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("network blocked"))
    try:
        for _, engine, ck, params, _ in fixtures():
            assert serpapi.search(engine, ck, **params), (engine, params)
        try:  # the bundle (not a demo) is cached but never pinned -> must count as expired
            serpapi.search("amazon_product", asin="B0GRV1RWMY")
            raise AssertionError("unpinned row served past its TTL")
        except ConnectionError:
            pass
    finally:
        serpapi.TTL, serpapi.httpx.get = real_ttl, real_get
    print("check ok: fixtures survive expiry, unpinned rows still expire")


if __name__ == "__main__":
    import sys

    pin_all()
    if "--demo-runs" in sys.argv:  # after a pre-warm: pin everything the chips read (listings, markets, trends, finance,
        # receipts engines, Groq receipts results, verdict and "What owners say" narration)
        pin_demo_runs()
    check()
