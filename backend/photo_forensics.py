"""Photo forensics: where else does this listing photo appear online? Evidence only: no verdict, no score.

Engines (chosen by the phase0/photo_probe run, 2026-09-29):
  - Google Lens exact_matches: mandatory; the widest net.
  - Bing reverse image: found real sites Lens missed (bol.com, fnac, jbhifi; apple.com for cr072572's render).
Dropped: Google Reverse Image (pages *about* the product, not the same photo) and Yandex (two-thirds off-topic).
Neither engine proves "the same photo" for generic subjects (a sealed white box): they return look-alikes. So every
signal counts only matches whose thumbnail is pixel-verified as the same photo (a 16x16 difference hash). Lens returns
at most 400 matches, so a count at the cap reads "400+" and is never a signal.

compute_photo_signals() turns the matches into three signals (A, stolen_own_photo, C). Still no score or verdict.
"""
import asyncio
import hashlib
import io
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps

import serpapi
from domains import classify, is_listing_page, registered_domain

TIMEOUT = 20       # s per engine: Lens took up to 12.2s live in the probe (10.2s by image_id), so 12s drops real results
MAX_UPLOAD = 500_000  # SerpApi /image limit, bytes
LENS_CAP = 400
# Same photo = dHash distance <= this, of 256 bits. Calibrated on photos labelled by eye in the Prompt 2 probe:
# the same photo re-served at another size scored 0-13; different photos of the same sealed box scored 59-116.
SAME_PHOTO = 24
MAX_VERIFY = 40  # thumbnails checked per signal. ponytail: bounds latency; raise it if evidence is ever cut off
USED_TEXT = re.compile(r"\bmy own\b|\bpersonal\b|private sale|signs of use|lightly used|gently used|pre-?owned|\bused\b", re.I)
SCAM_WORDS = re.compile(r"scam|fake|fraud|counterfeit", re.I)
SIGNALS = ("stock_photo_on_private_listing", "stolen_own_photo", "photo_on_scam_reports")
EBAY_ITEM = re.compile(r"ebay\.[a-z.]+/itm/(?:[^/?]+/)?(\d{9,})")
# Page/thumbnail fetches (not SerpApi). A client, so tests that block module-level httpx calls don't block these.
_web = httpx.Client(timeout=5, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
CLASS_ORDER = ["manufacturer", "known_retailer", "marketplace_listing", "forum_or_scam_report", "review_or_press", "unknown"]

# engine -> result list key, field holding the page URL, param for an image URL / an uploaded image_id, fixed params
ENGINES = {
    "google_lens": {"rows": "exact_matches", "url_field": "link", "by_url": "url", "by_id": "image_id",
                    "fixed": {"type": "exact_matches"}},
    # `link` is a bing.com detail page; the page with the photo is `source`. Bing has no upload id: URL input only.
    "bing_reverse_image": {"rows": "pages_with_this_image", "url_field": "source", "by_url": "image_url", "by_id": None,
                           "fixed": {}},
}


def identity(url=None, data=None):
    """Stable cache identity: sha256 of the URL, or of the ORIGINAL bytes (an upload's image_id differs every time)."""
    return hashlib.sha256(url.encode() if url else data).hexdigest()


def cache_key(engine, ident):
    return {"image": ident, **ENGINES[engine]["fixed"]}


def _jpeg_under_limit(data):
    """Re-encode as JPEG under MAX_UPLOAD: lower quality first, then shrink 25% at a time."""
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    im.thumbnail((2000, 2000))
    while True:
        for quality in (85, 70, 55):
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, optimize=True)
            if buf.tell() < MAX_UPLOAD:
                return buf.getvalue()
        im = im.resize((im.width * 3 // 4, im.height * 3 // 4))


def _dhash(data):
    """256-bit difference hash: survives resizing and re-compression, changes with a different photo."""
    px = Image.open(io.BytesIO(data)).convert("L").resize((17, 16)).load()
    return f"{int(''.join('1' if px[x, y] > px[x + 1, y] else '0' for y in range(16) for x in range(16)), 2):064x}"


def _distance(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _photo_hash(url=None, data=None):
    try:
        return _dhash(data or _web.get(url).raise_for_status().content)
    except Exception:
        return None


def _upload(data):
    r = httpx.post("https://serpapi.com/image", data={"api_key": os.environ["SERPAPI_KEY"]},
                   files={"image": ("image.jpg", _jpeg_under_limit(data), "image/jpeg")}, timeout=30)
    r.raise_for_status()
    return r.json()["image_id"]


def _url_key(url):
    """Dedupe key: host + path. ponytail: drops the query string (tracking params), so a site that puts the item id
    only in ?id= would merge its pages; none seen in the probe data."""
    u = urlparse(url)
    return (u.hostname or "").removeprefix("www.") + u.path.rstrip("/")


STREAM_MATCHES, MATCH_BATCH = 36, 12  # matches streamed to the UI (best classes first), per event


def _emit(emit, **event):
    """Live progress for the UI (SSE via the orchestrator). Never lets a UI problem break the check."""
    if emit:
        try:
            emit(event)
        except Exception:
            pass


async def _call(engine, ident, params, emit=None):
    """One engine, through serpapi_cache. Never raises: returns (response | None, stats). Emits when it finishes."""
    start = time.monotonic()
    key = cache_key(engine, ident)
    was_cached = await asyncio.to_thread(serpapi.cached, engine, key)
    try:
        data = was_cached or await asyncio.wait_for(asyncio.to_thread(serpapi.search, engine, key, **params), TIMEOUT)
        st = {"ms": round((time.monotonic() - start) * 1000), "cached": bool(was_cached)}
        rows = len([r for r in data.get(ENGINES[engine]["rows"], []) if r.get(ENGINES[engine]["url_field"])])
        _emit(emit, type="forensics_engine_done", engine=engine, matches_found=rows, ms=st["ms"],
              capped=engine == "google_lens" and rows >= LENS_CAP)
        return data, st
    except Exception as e:  # timeout, HTTP or SerpApi error: this engine is unavailable, the others carry on
        st = {"ms": round((time.monotonic() - start) * 1000), "cached": False,
              "reason": f"{type(e).__name__}: {e}"[:200] if str(e) else type(e).__name__}
        _emit(emit, type="forensics_engine_done", engine=engine, matches_found=0, ms=st["ms"], unavailable=st["reason"])
        return None, st


async def run(image_url=None, image_bytes=None, emit=None):
    """Exactly one of image_url (a listing photo) or image_bytes (an upload). emit(event) streams progress."""
    assert (image_url is None) != (image_bytes is None), "pass exactly one of image_url / image_bytes"
    ident = identity(image_url, image_bytes)
    params, unavailable = {}, []
    if image_url:
        params = {e: {c["by_url"]: image_url, **c["fixed"]} for e, c in ENGINES.items()}
    else:
        for e, c in ENGINES.items():
            if not c["by_id"] and not serpapi.cached(e, cache_key(e, ident)):
                unavailable.append({"engine": e, "reason": "needs a public image URL; an uploaded image has none"})
        need = [e for e, c in ENGINES.items() if c["by_id"] and not serpapi.cached(e, cache_key(e, ident))]
        image_id = None
        if need:  # upload only on a cache miss, so a rehearsal re-run makes no network call at all
            try:
                image_id = await asyncio.wait_for(asyncio.to_thread(_upload, image_bytes), TIMEOUT)
            except Exception as e:
                unavailable += [{"engine": e_, "reason": f"upload failed: {type(e).__name__}: {e}"[:200]} for e_ in need]
        params = {e: {c["by_id"]: image_id, **c["fixed"]} for e, c in ENGINES.items()
                  if c["by_id"] and e not in {u["engine"] for u in unavailable}}

    engines = list(params)
    _emit(emit, type="forensics_start", engines=engines, input="url" if image_url else "upload",
          image=image_url, unavailable=unavailable)
    photo_hash, *results = await asyncio.gather(asyncio.to_thread(_photo_hash, image_url, image_bytes),
                                                *(_call(e, ident, params[e], emit) for e in engines))

    matches, stats = {}, {}
    for engine, (data, st) in zip(engines, results):
        if data is None:
            unavailable.append({"engine": engine, "reason": st.pop("reason")})
            continue
        c = ENGINES[engine]
        rows = [r for r in data.get(c["rows"], []) if r.get(c["url_field"])]
        stats[engine] = {**st, "rows": len(rows), "capped": engine == "google_lens" and len(rows) >= LENS_CAP}
        for r in rows:
            url = r[c["url_field"]]
            m = matches.setdefault((registered_domain(url), _url_key(url)), {
                "domain": registered_domain(url), "url": url, "title": None, "thumbnail": None, "price": None,
                "date": None, "engines_found_on": [], "domain_class": classify(url)})
            m["engines_found_on"].append(engine)
            m["title"] = m["title"] or r.get("title")
            m["thumbnail"] = m["thumbnail"] or r.get("thumbnail")
            m["price"] = m["price"] or r.get("extracted_price")
            if engine == "google_lens":  # Bing's `date` is when Bing crawled the page, not when the photo appeared
                m["date"] = m["date"] or r.get("date")

    ordered = sorted(matches.values(), key=lambda m: CLASS_ORDER.index(m["domain_class"]))
    capped = any(s["capped"] for s in stats.values())
    total_display = f"{len(ordered)}+" if capped else str(len(ordered))
    for i in range(0, min(len(ordered), STREAM_MATCHES), MATCH_BATCH):
        _emit(emit, type="forensics_matches", total_display=total_display, matches=[
            {k: m[k] for k in ("domain", "domain_class", "thumbnail", "url", "title", "engines_found_on")}
            for m in ordered[i:i + MATCH_BATCH]])
    return {
        "image_identity": ident, "input": "url" if image_url else "upload", "photo_hash": photo_hash,
        "engines_used": list(stats), "engines_unavailable": unavailable, "engine_stats": stats,
        "total_matches": len(ordered), "total_display": total_display,
        "matches_by_class": {c: sum(m["domain_class"] == c for m in ordered) for c in CLASS_ORDER},
        "matches": ordered,
    }


def _signal(name, status, reason, evidence=()):
    keep = ("domain", "domain_class", "url", "title", "thumbnail", "price", "photo_distance")
    return {"signal": name, "status": status, "reason": reason, "evidence": [{k: m.get(k) for k in keep} for m in evidence]}


def _same_photo(photo_hash, matches):
    """The matches whose thumbnail is the same photo as the listing's, with the distance attached."""
    def check(m):
        h = _photo_hash(m["thumbnail"])
        return h and _distance(photo_hash, h) <= SAME_PHOTO and {**m, "photo_distance": _distance(photo_hash, h)}
    with ThreadPoolExecutor(16) as pool:
        return [m for m in pool.map(check, [m for m in matches if m.get("thumbnail")][:MAX_VERIFY]) if m]


def _where(ms):
    return ", ".join(sorted({m["domain"] for m in ms}))


def compute_photo_signals(result, listing):
    """listing: trust.py's resolved listing (+ condition_bucket, optional description). Returns one entry per signal:
    {signal, status: fired | not_fired | skipped | unavailable, reason, evidence}. Only fired signals count; unavailable
    is never a penalty. The false-positive guard: a maker/retailer photo is normal unless the listing presents as used."""
    if not result["engines_used"]:
        return [_signal(n, "unavailable", "The photo search was unavailable, so this wasn't checked (not counted)")
                for n in SIGNALS]
    if not result.get("photo_hash"):
        return [_signal(n, "unavailable", "The listing photo couldn't be loaded to compare, so this wasn't checked "
                                          "(not counted)") for n in SIGNALS]
    h, matches = result["photo_hash"], result["matches"]
    in_class = lambda *cs: [m for m in matches if m["domain_class"] in cs]
    out = []

    if listing.get("seller_is_platform"):  # reduced scrutiny: a store showing the maker's photo is normal
        why = f"Sold by {listing.get('seller')} itself: a store using the maker's photo is normal"
        out += [_signal(SIGNALS[0], "skipped", why), _signal(SIGNALS[1], "skipped", why)]
        stock = []
    else:
        stock = _same_photo(h, in_class("manufacturer", "known_retailer"))
        condition, bucket = listing.get("condition") or "unknown", listing.get("condition_bucket")
        cue = USED_TEXT.search(f"{listing.get('title') or ''} {listing.get('description') or ''}")
        private = bucket == "used" or (bucket not in ("refurbished", "parts") and cue)
        if not private:
            out.append(_signal(SIGNALS[0], "not_fired", f"Listed as {condition}: a maker or retailer photo is normal here"))
        elif stock:
            said = f" and its description says \"{cue.group(0)}\"" if cue else ""
            out.append(_signal(SIGNALS[0], "fired",
                f"Listed as {condition}{said}, but its photo is the same image used on {len(stock)} maker/retailer "
                f"page(s) ({_where(stock)}). A used item is normally shown in photos of the actual unit.", stock))
        else:
            out.append(_signal(SIGNALS[0], "not_fired", f"Listed as {condition}, and its photo isn't on any maker or retailer page"))

        if stock:
            out.append(_signal(SIGNALS[1], "not_fired", "The photo is a maker/retailer image, not the seller's own"))
        else:
            own = listing.get("id") if listing.get("marketplace") == "ebay" else None
            # Other sellers' individual listings only: a category/search page showing this listing's own thumbnail
            # is not a copy (airpods-ebay's photo sits on 3 eBay category pages, as its own listing).
            others = [m for m in in_class("marketplace_listing") if is_listing_page(m["url"])
                      and not (own and (x := EBAY_ITEM.search(m["url"])) and x.group(1) == own)]
            copies = _same_photo(h, others)
            out.append(_signal(SIGNALS[1], "fired",
                f"The photo looks like the seller's own (it's on no maker or retailer page), yet the same photo is on "
                f"{len(copies)} other marketplace listing(s) ({_where(copies)}). Either it was copied from another "
                f"listing, or the item is cross-posted elsewhere.", copies) if copies else
                _signal(SIGNALS[1], "not_fired", "The photo isn't on any other marketplace listing"))

    reports = _same_photo(h, [m for m in in_class("forum_or_scam_report") if SCAM_WORDS.search(m.get("title") or "")])
    out.append(_signal(SIGNALS[2], "fired",
        f"The same photo appears in {len(reports)} forum/report post(s) that mention scams or fakes ({_where(reports)})",
        reports) if reports else _signal(SIGNALS[2], "not_fired", "The photo isn't in any forum post about scams or fakes"))
    return out


if __name__ == "__main__":
    import sys

    from demo_products import DEMO_CHIPS as DEMO_PRODUCTS  # the demo set only

    if "--signals" in sys.argv:
        # Replay: listings from cached data (5 listing records are past the TTL), SerpApi network blocked -> 0 searches.
        # Page thumbnails still load over the web (_web), which is not SerpApi.
        from datetime import timedelta

        import trust
        serpapi.TTL = timedelta(days=3650)
        serpapi.httpx.get = httpx.post = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("SerpApi blocked"))
        want = {"sony-xm5": set(), "honeywell-heater": set(), "switch2": set()}
        failures = []
        for key, p in DEMO_PRODUCTS.items():
            if p["marketplace"] == "amazon":
                listing = trust._listing_amazon(p["listing_id"])
            else:
                listing = trust._listing_ebay(p["listing_id"])
                listing["description"] = serpapi.search("ebay_product", product_id=p["listing_id"])["product_results"].get("short_description")
            listing["condition_bucket"] = trust._condition(listing["condition"])
            r = asyncio.run(run(image_url=p["image"]))
            t0 = time.monotonic()
            sigs = compute_photo_signals(r, listing)
            fired = {x["signal"] for x in sigs if x["status"] == "fired"}
            print(f"\n=== {key}  ({listing['condition']}, seller {listing['seller']}, first-party {listing['seller_is_platform']})"
                  f"  matches {r['total_display']}  signals {time.monotonic() - t0:.1f}s  fired: {sorted(fired) or 'none'}")
            if key == "airpods-ebay":
                own_photo = (listing, r)
            for x in sigs:
                print(f"  [{x['status']:9}] {x['signal']}: {x['reason']}")
                for e in x["evidence"][:6]:
                    print(f"        d={e['photo_distance']:<3} {e['domain_class']:15} {e['domain']:18} {(e['title'] or '')[:60]}")
            if key in want and fired != want[key]:
                failures.append(f"{key} fired {fired}")
            if key == "airpods-openbox" and not fired & {"stock_photo_on_private_listing", "stolen_own_photo"}:
                failures.append("walk-away candidate fired neither A nor stolen_own_photo")

        # Synthetic: airpods-ebay's own photo (a real unit, on no maker/retailer page) planted as the thumbnail of another
        # listing and of a scam-report post must fire stolen_own_photo and C; a look-alike photo must not.
        listing, r = own_photo
        img, lookalike = DEMO_PRODUCTS["airpods-ebay"]["image"], DEMO_PRODUCTS["airpods-openbox"]["image"]
        fake = lambda url, cls, title, thumb: {"domain": registered_domain(url), "url": url, "title": title,
                                               "thumbnail": thumb, "price": None, "domain_class": cls}
        planted = {**r, "matches": r["matches"] + [
            fake("https://jp.mercari.com/item/m111", "marketplace_listing", "AirPods Pro 2", img),
            fake("https://www.reddit.com/r/airpods/x", "forum_or_scam_report", "Are these fake AirPods? Scam?", img),
            fake("https://www.mercari.com/us/item/m222", "marketplace_listing", "AirPods Pro 2", lookalike)]}
        got = {x["signal"]: x for x in compute_photo_signals(planted, listing)}
        checks = {
            "planted copy fires stolen_own_photo": got["stolen_own_photo"]["status"] == "fired",
            "only the identical photo is evidence": [e["url"] for e in got["stolen_own_photo"]["evidence"]] == ["https://jp.mercari.com/item/m111"],
            "planted scam post fires C": got["photo_on_scam_reports"]["status"] == "fired",
            "all engines failed -> all unavailable": all(x["status"] == "unavailable" for x in compute_photo_signals(
                {**r, "engines_used": [], "matches": []}, listing)),
        }
        for name, ok in checks.items():
            print(f"synthetic: {name}: {'ok' if ok else 'FAILED'}")
        failures += [n for n, ok in checks.items() if not ok]
        print("\nEXPECTATION FAILURES:", failures or "none")
        sys.exit(1 if failures else 0)

    # Offline: a 3000x3000 noise image (~27 MB raw, incompressible) must still come out a valid JPEG under the limit.
    noise = Image.frombytes("RGB", (3000, 3000), os.urandom(3000 * 3000 * 3))
    buf = io.BytesIO()
    noise.save(buf, "PNG")
    small = _jpeg_under_limit(buf.getvalue())
    assert len(small) < MAX_UPLOAD and Image.open(io.BytesIO(small)).format == "JPEG", len(small)
    print(f"compress ok: {len(buf.getvalue()):,} B png -> {len(small):,} B jpeg")

    def account():
        return httpx.get("https://serpapi.com/account.json", params={"api_key": os.environ["SERPAPI_KEY"]},
                         timeout=30).json()["this_month_usage"]

    inputs = {k: {"image_url": p["image"]} for k, p in DEMO_PRODUCTS.items() if p.get("image")}
    if "--upload" in sys.argv:  # the bytes path: one real upload + one Lens search the first time
        inputs["upload:airpods-openbox"] = {"image_bytes": open(sys.argv[sys.argv.index("--upload") + 1], "rb").read()}
    for attempt in ("first run", "second run"):
        before = account()
        print(f"\n##### {attempt}")
        if attempt == "second run":  # prove zero SerpApi calls: any search or upload not served from cache fails loudly
            serpapi.httpx.get = httpx.post = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("network blocked"))
        for name, kw in inputs.items():
            r = asyncio.run(run(**kw))
            s = r["engine_stats"]
            print(f"{name:24} total {r['total_display']:>5} | {r['matches_by_class']}")
            print(f"{'':24} " + " | ".join(f"{e}: {v['rows']} rows {v['ms']}ms{' cached' if v['cached'] else ''}"
                                           for e, v in s.items())
                  + "".join(f" | {u['engine']} UNAVAILABLE ({u['reason']})" for u in r["engines_unavailable"]))
            assert not r["engines_unavailable"] or kw.get("image_bytes"), r["engines_unavailable"]
            if attempt == "second run":
                assert all(v["cached"] for v in s.values()), f"{name}: not served from cache"
        if attempt == "first run":  # the account counter lags a few seconds behind; it's a report, not the proof
            print(f"SerpApi account usage moved by {account() - before} (may lag)")
    print("\nsecond run: every engine served from serpapi_cache with the network blocked -> 0 searches")
