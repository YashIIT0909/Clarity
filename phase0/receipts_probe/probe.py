"""Owner Receipts probe (Prompt 1): YouTube Search, YouTube Video Transcript, Google Forums per product; Walmart
Reviews for one product. Locale US. Raw JSON -> out/<product>_<engine>[_<id>].json. Existing files are reused (re-runs
cost 0 searches).

Two stages, because the transcripts to fetch are picked by reading the search results:
  ../../backend/.venv/bin/python probe.py search                         # YouTube + Forums x3, Walmart x1
  ../../backend/.venv/bin/python probe.py transcripts <product> <video_id>...
  ../../backend/.venv/bin/python probe.py report                         # tables from saved JSON, 0 searches
(run from this dir)"""
import json
import os
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
BACKEND = HERE.parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

# Clean product name as a reviewer titles it / a shopper searches it (same as the successor probe).
NAMES = {"sony-xm5": "sony wh-1000xm5", "switch2": "nintendo switch 2", "honeywell-heater": "honeywell 360 heater"}
# Walmart us_item_id from cached Immersive Product rows (0 searches to find). One product only: Sony, the one whose
# Amazon row has no review text at all (star breakdown only), so it's where Walmart could add the most.
WALMART = {"sony-xm5": "386006068"}  # walmart.com/ip/Sony-WH-1000XM5-...-Black/386006068
THIN_FORUMS = 5  # fewer organic results than this -> also try "<name> issues"

# Param names from SerpApi docs (2026-09-30): youtube-search-api, youtube-video-transcript, google-forums-api,
# walmart-product-reviews-api.
ENGINES = {
    "youtube": lambda n: {"engine": "youtube", "search_query": f"{n} review", "gl": "us", "hl": "en"},
    "forums_problems": lambda n: {"engine": "google_forums", "q": f"{n} problems", "gl": "us", "hl": "en"},
    "forums_issues": lambda n: {"engine": "google_forums", "q": f"{n} issues", "gl": "us", "hl": "en"},
}


def call(params, path):
    if path.exists():
        return json.loads(path.read_text())
    t = time.monotonic()
    try:
        data = httpx.get("https://serpapi.com/search.json",
                         params={**params, "api_key": os.environ["SERPAPI_KEY"]}, timeout=240).json()
    except Exception as e:  # keep going; record the failure
        data = {"error": repr(e)}
    data["_probe_latency_s"] = round(time.monotonic() - t, 2)
    path.write_text(json.dumps(data, indent=1))
    return data


def search():
    for k, name in NAMES.items():
        for eng in ("youtube", "forums_problems"):
            d = call(ENGINES[eng](name), OUT / f"{k}_{eng}.json")
            print(f"{k:17} {eng:16} {d.get('error') or 'ok'}  {d['_probe_latency_s']}s")
        if len(call(ENGINES["forums_problems"](name), OUT / f"{k}_forums_problems.json").get("organic_results") or []) < THIN_FORUMS:
            d = call(ENGINES["forums_issues"](name), OUT / f"{k}_forums_issues.json")
            print(f"{k:17} {'forums_issues':16} {d.get('error') or 'ok'}  {d['_probe_latency_s']}s")
    for k, item in WALMART.items():
        d = call({"engine": "walmart_product_reviews", "product_id": item}, OUT / f"{k}_walmart_reviews.json")
        print(f"{k:17} {'walmart_reviews':16} {d.get('error') or 'ok'}  {d['_probe_latency_s']}s")


def transcripts(key, ids):
    # language_code=en is the documented default; set explicitly so the probe shows what the fallback does.
    for v in ids:
        d = call({"engine": "youtube_video_transcript", "v": v, "language_code": "en"}, OUT / f"{key}_transcript_{v}.json")
        print(f"{key:17} transcript {v}  {d.get('error') or str(len(d.get('transcript') or [])) + ' segments'}  "
              f"{d['_probe_latency_s']}s")


def report():
    for k in NAMES:
        yt = json.loads((OUT / f"{k}_youtube.json").read_text())
        print(f"\n=== {k}: YouTube top 10 (of {len(yt.get('video_results') or [])} video_results; "
              f"{len(yt.get('shorts_results') or [])} shorts_results blocks)")
        for i, v in enumerate((yt.get("video_results") or [])[:10], 1):
            print(f" {i:2}. {v.get('title')!r}\n     {v['channel']['name']} | {v.get('views')} views | "
                  f"{v.get('published_date')} | {v.get('length')} | {v.get('video_id')}")
        for f in sorted(OUT.glob(f"{k}_transcript_*.json")):
            d = json.loads(f.read_text())
            seg = d.get("transcript") or []
            avail = [(a.get("language_code"), a.get("type"), a.get("selected")) for a in d.get("available_transcripts") or []]
            print(f"  transcript {f.stem.split('_')[-1]}: {d.get('error') or ''} {len(seg)} segments, "
                  f"last at {seg[-1]['start_time_text'] if seg else '-'}, {len(' '.join(s['snippet'] for s in seg).split())} "
                  f"words, chapters {len(d.get('chapters') or [])}, available {avail}")
            if seg:
                print(f"    first: {seg[0]}")
        for eng in ("forums_problems", "forums_issues"):
            f = OUT / f"{k}_{eng}.json"
            if not f.exists():
                continue
            d = json.loads(f.read_text())
            print(f"  {eng}: {len(d.get('organic_results') or [])} results {d.get('error') or ''}")
            for r in d.get("organic_results") or []:
                print(f"   - {r.get('title')!r} | {r.get('source')} | {r.get('displayed_meta') or r.get('date')}")


if __name__ == "__main__":
    {"search": lambda: search(), "transcripts": lambda: transcripts(sys.argv[2], sys.argv[3:]),
     "report": lambda: report()}[sys.argv[1]]()
