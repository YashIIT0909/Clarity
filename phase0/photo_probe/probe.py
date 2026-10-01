"""Photo-forensics probe: Lens (3 types) + Google/Bing/Yandex reverse image on each demo listing's main photo.
Raw JSON -> out/<product>_<engine>.json. Existing files are reused (re-runs cost 0 searches).
Run: ../../backend/.venv/bin/python probe.py   (from this dir)"""
import json
import os
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
BACKEND = HERE.parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # serpapi.py's load_dotenv() reads backend/.env
from demo_products import DEMO_PRODUCTS  # noqa: E402
from serpapi import search  # noqa: E402  (cached; listing data is already in serpapi_cache)

OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
KEY = os.environ["SERPAPI_KEY"]

# Param names from SerpApi docs (2026-09-29).
ENGINES = {
    "lens_exact_matches": lambda u: {"engine": "google_lens", "url": u, "type": "exact_matches"},
    "lens_visual_matches": lambda u: {"engine": "google_lens", "url": u, "type": "visual_matches"},
    "lens_products": lambda u: {"engine": "google_lens", "url": u, "type": "products"},
    "google_reverse_image": lambda u: {"engine": "google_reverse_image", "image_url": u},
    "bing_reverse_image": lambda u: {"engine": "bing_reverse_image", "image_url": u},
    "yandex_reverse_image": lambda u: {"engine": "yandex_images", "url": u, "tab": "about"},
}


def main_image(p):
    """Amazon: product_results.thumbnails[0] (full-res main image).
    eBay: product_results.media[0].image[-1] (largest size of the first photo)."""
    if p["marketplace"] == "amazon":
        return search("amazon_product", asin=p["listing_id"])["product_results"]["thumbnails"][0]
    return search("ebay_product", product_id=p["listing_id"])["product_results"]["media"][0]["image"][-1]["link"]


def call(params, path):
    if path.exists():
        return json.loads(path.read_text())
    t = time.monotonic()
    try:
        data = httpx.get("https://serpapi.com/search.json", params={**params, "api_key": KEY}, timeout=90).json()
    except Exception as e:  # keep going; record the failure
        data = {"error": repr(e)}
    data["_probe_latency_s"] = round(time.monotonic() - t, 2)
    path.write_text(json.dumps(data, indent=1))
    return data


def run_all():
    images = {k: main_image(p) for k, p in DEMO_PRODUCTS.items()}
    (OUT / "images.json").write_text(json.dumps(images, indent=1))
    for k, url in images.items():
        print(k, url)
        for name, mk in ENGINES.items():
            d = call(mk(url), OUT / f"{k}_{name}.json")
            print(f"  {name:22} {d.get('_probe_latency_s')}s {d.get('error', '')}")
    return images


def upload_test(product="sony-xm5"):
    """Download one demo image, compress <500 KB, POST to serpapi.com/image, run Lens exact_matches by image_id."""
    out = OUT / f"{product}_lens_exact_matches_upload.json"
    if out.exists():
        return json.loads(out.read_text())
    url = json.loads((OUT / "images.json").read_text())[product]
    raw, small = OUT / f"{product}_orig.jpg", OUT / f"{product}_small.jpg"
    raw.write_bytes(httpx.get(url, timeout=30, follow_redirects=True).content)
    # ImageMagick is already on the machine; Pillow is not in the backend venv.
    os.system(f"magick {raw} -resize '1200x1200>' -quality 80 -define jpeg:extent=450kb {small}")
    print("orig", raw.stat().st_size, "bytes -> compressed", small.stat().st_size, "bytes")
    assert small.stat().st_size < 500_000
    t = time.monotonic()
    r = httpx.post("https://serpapi.com/image", data={"api_key": KEY},
                   files={"image": (small.name, small.read_bytes(), "image/jpeg")}, timeout=60)
    print("upload", r.status_code, round(time.monotonic() - t, 2), "s", r.text[:300])
    up = r.json()
    (OUT / "upload_response.json").write_text(json.dumps(up, indent=1))
    image_id = up.get("image_id") or up.get("id")
    t = time.monotonic()
    d = httpx.get("https://serpapi.com/search.json", timeout=90, params={
        "engine": "google_lens", "image_id": image_id, "type": "exact_matches", "api_key": KEY}).json()
    d["_probe_latency_s"] = round(time.monotonic() - t, 2)
    out.write_text(json.dumps(d, indent=1))
    return d


if __name__ == "__main__":
    run_all()
    upload_test()
