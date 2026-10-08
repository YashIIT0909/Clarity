"""Trust agent: listing + market data -> pass/fail with confidence. Rule-based, no ML.

Each failed check subtracts its severity weight from a trust score of 100. Score below PASS_SCORE fails;
distance from that line sets the confidence (same 0.50..0.95 scale as timing.py).
"""
import asyncio
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import photo_forensics
import store_reputation
from domains import classify
from serpapi import db, search

# Thresholds (tune here).
# Price bands are in deviations from this product's own retail prices: sigma = IQR / 1.349 (robust to the
# junk prices Shopping lets through, which inflate a plain stdev; MAD collapses to 0 on duplicate prices).
Z_TOO_CHEAP = 2.0       # listing > 2 sigma below median -> critical
Z_TOO_EXPENSIVE = 2.0   # listing > 2 sigma above median -> flag (mild overpricing is a timing/value question, not trust)
MIN_SPREAD = 0.05       # sigma floor as a fraction of median; ponytail: guards a zero IQR on 3-5 identical prices
MAX_SPREAD = 0.25       # sigma cap: on 3-5 prices the IQR is ~the full range, so one junk price could hide any listing.
                        # New units of one product don't genuinely vary more than this; wider means a contaminated sample.
IN_LINE = 0.10          # within ±10% of median reads as "in line"
MIN_REVIEWS = 50
MIN_RATING = 3.5
MIN_SELLER_SALES = 20
MIN_MARKET_PRICES = 3
MIN_USED_COMPARABLES = 8  # secondhand band: with 8+ prices each quartile holds 2+, so one odd listing can't set the IQR.
                          # AirPods Pro 2 (2026-09-28 eBay search): used+open box 17, refurbished 5 -> refurbished unavailable
SUDDEN_DROP = 0.25      # listing >= 25% below its own median price in earlier Clarity checks -> flag

# Severity weights (points off 100). To keep the old "any critical, or 2+ flags = FAIL" rule exactly, each
# non-critical weight must be <= 100 - PASS_SCORE (one alone passes) and any two must sum > it (two fail).
WEIGHTS = {
    "too_cheap": 60,          # critical: fails on its own
    "no_returns": 35,
    "unproven_seller": 35,
    "low_rating": 30,
    "too_expensive": 25,
    "sudden_drop": 30,        # a recent cut is the hijacked-listing pattern; a long-standing low price isn't flagged here
    "seller_pattern": 35,     # seller's other listings of this product are also implausibly cheap
    "few_reviews": 25,
    "market_unknown": 25,
    # Photo and store evidence (photo_forensics.py / store_reputation.py). None fails a listing on its own; each fails
    # it together with any one other flag. Ads and photo counts are context only and have no weight.
    "stock_photo_on_private_listing": 35,  # "used" listing shown with the maker's pixel-identical photo
    "stolen_own_photo": 30,        # own-looking photo also on another seller's listing; cross-posting looks the same
    "photo_on_scam_reports": 30,   # the photo is in a thread about scams/fakes, which may be *asking* whether it's fake
    "store_complaints": 35,        # 2+ independent reports of non-delivery/fraud about an unknown shop
    "no_web_footprint": 25,        # weakest: honest new shops have no footprint either
}
EVIDENCE_TIMEOUT = 30  # s trust waits for photo forensics / store reputation (run in parallel with the market check)
PASS_SCORE = 60
# Waived when the marketplace itself sells and ships the item: its price moves are repricing, not a scam signal.
# Review/rating checks still apply. eBay has no first-party seller, so this only affects Amazon today.
FIRST_PARTY_WAIVED = {"too_cheap", "too_expensive", "market_unknown", "sudden_drop"}

# Titles containing these are accessories/parts, not the product itself.
ACCESSORY = re.compile(
    r"replacement|\breplace\b|case only|\bleft\b|\bright\b|\bfor\b|cover|skin|sticker|ear ?tips|strap|stand\b"
    r"|adapter|cable|charger|joy-?con|controller|dock|carrying"
    r"|for parts|empty box|box only|dummy|\bcamera\b|\bgame\b"
    r"|\d+ ?-?pack\b",
    re.I,
)
# ...and these are secondhand: excluded from a new-retail band, expected in a used one.
SECONDHAND = re.compile(r"refurb|renewed|restored|used|pre-?owned|open box", re.I)
# Peer-to-peer marketplaces: same kind of listing we're judging, so not a retail market reference.
PEER_MARKETS = re.compile(r"mercari|poshmark|ebay|bonanza|jawa|facebook|offerup|shein|whatnot", re.I)
# Known retailers. Their product pages list many stores; only these (or their marketplace sellers, "Walmart - X")
# may supply the reference product whose store list joins the market sample.
MAJOR_RETAILERS = {
    "best buy", "walmart", "target", "gamestop", "amazon.com", "costco", "home depot", "the home depot",
    "lowe's", "b&h photo-video-audio", "sam's club", "bj's wholesale club", "apple", "sony", "nintendo",
    "tractor supply company", "ace hardware", "staples", "office depot", "kohl's", "macy's",
}


def _is_major(store):
    """The retailer itself ("Walmart", "Walmart Supercenter"), never a marketplace seller on its platform
    ("Walmart - SomeSeller"), which Google Shopping names with a " - " suffix."""
    s = (store or "").lower()
    return " - " not in s and any(s == m or s.startswith(m + " ") for m in MAJOR_RETAILERS)


def _condition(text):
    """Condition bucket a price is compared within: new | used (incl. open box) | refurbished | parts.
    Open box sits with used: AirPods Pro 2 open-box listings ($45-65) priced like used ($65 median), not new ($99)."""
    c = (text or "").lower()
    if "parts" in c:
        return "parts"
    if re.search(r"refurb|renewed", c):
        return "refurbished"
    if re.search(r"used|pre-?owned|open box|new other|new \(other\)", c):
        return "used"
    return "new"


def _listing_amazon(asin):
    d = search("amazon_product", asin=asin)
    p = d["product_results"]
    offer = next(iter((d.get("purchase_options") or {}).values()), {})
    seller = ((offer.get("features") or {}).get("shipper_seller") or {}).get("text", "unknown")
    return {
        "marketplace": "amazon", "id": asin, "title": p["title"],
        "price": p.get("extracted_price") or offer.get("extracted_price"),
        "rating": p.get("rating"), "reviews": p.get("reviews") or 0,
        "image": next(iter(p.get("thumbnails") or []), None),  # main photo, full size
        "seller": seller, "seller_is_platform": seller == "Amazon.com",  # combined Shipper/Seller field: Amazon sells AND ships
        # No per-offer condition field: the buy box is new unless the ASIN itself is a Renewed one or only used offers exist.
        "condition": "refurbished" if re.search(r"renewed|refurbished", p["title"], re.I)
                     else "new" if {"buy_new", "single_offer"} & set(d.get("purchase_options") or {}) else "used",
        "observed_at": d["_fetched_at"],
    }


def _listing_ebay(item_id):
    d = search("ebay_product", product_id=item_id)
    p, s = d["product_results"], d.get("seller_results") or {}
    returns = " ".join(sn.get("text", "") for grp in (p.get("returns") or []) for blk in grp for sn in blk.get("snippets", []))
    return {
        "marketplace": "ebay", "id": item_id, "title": p["title"], "description": p.get("short_description"),
        "image": ((p.get("media") or [{}])[0].get("image") or [{}])[-1].get("link"),  # first photo, largest size
        # Auction-only and multi-option listings have no single Buy It Now price: None (price check skipped).
        "price": (((p.get("buy") or {}).get("buy_it_now") or {}).get("price") or {}).get("amount"),
        "seller": s.get("username", "unknown"), "seller_is_platform": False,
        "seller_sales": s.get("sold_count") or 0, "seller_feedback": s.get("rating") or 0,
        "seller_joined": s.get("joined_at"),
        "returns_accepted": "no returns" not in returns.lower(),
        "condition": (p.get("condition") or "unknown").lower(), "observed_at": d["_fetched_at"],
    }


def _matcher(match, exclude=None, secondhand=False):
    """Title test: is this the product itself (not an accessory, part, bundle, or - unless secondhand - a used unit)?"""
    extra = re.compile(exclude, re.I) if exclude else None
    return lambda t: (
        all(re.search(m, t or "", re.I) for m in match)
        and not ACCESSORY.search(t or "")
        and (secondhand or not SECONDHAND.search(t or ""))
        and not (extra and extra.search(t or ""))
    )


def _seller_listings(listing, query, is_match):
    """The same seller's other listings of this product in the listing's condition bucket, from an eBay search for it.
    ponytail: SerpApi has no by-seller listing query (ebay_seller returns a profile only, _ssn is ignored), so this
    only sees sellers who rank in the first results page. Amazon results carry no seller at all -> always []."""
    if listing["marketplace"] != "ebay":
        return []
    return [
        {"id": x.get("product_id"), "title": x.get("title"), "price": x["price"]["extracted"]}
        for x in search("ebay", ebay_domain="ebay.com", _nkw=query).get("organic_results", [])
        if (x.get("seller") or {}).get("username") == listing["seller"] and x.get("product_id") != listing["id"]
        and (x.get("price") or {}).get("extracted")  # price ranges (multi-variant listings) have no single price
        and _condition(x.get("condition")) == listing["condition_bucket"] and is_match(x.get("title"))
    ]


def _market(query, match, exclude=None):
    """Median new-condition retail price: matched Google Shopping results + the matched product's Immersive store list."""
    is_match = _matcher(match, exclude)
    results = [
        r for r in search("google_shopping", q=query, gl="us", hl="en").get("shopping_results", [])
        if not r.get("second_hand_condition") and not PEER_MARKETS.search(r.get("source") or "")
    ]
    # One price per source: the reference product's own listing also appears in its store list.
    by_source = {r.get("source") or r.get("title"): r["extracted_price"]
                 for r in results if r.get("extracted_price") and is_match(r.get("title"))}
    sources = len(by_source)

    # The reference product's store list is merged into the market, so it must be on a known retailer's platform
    # ("Walmart - SomeSeller" is on walmart.com). An unknown domain with a matching title never gets that role.
    hero = next((r for r in results if is_match(r.get("title")) and r.get("immersive_product_page_token")
                 and (r.get("source") or "").lower().split(" - ")[0] in MAJOR_RETAILERS), None)
    stores, observed_at = [], None
    if hero:
        imm = search("google_immersive_product", page_token=hero["immersive_product_page_token"])
        observed_at = imm["_fetched_at"]
        # link + major feed "where to buy instead" (alternatives.py); the market sample still takes every store.
        stores = [
            {"store": s.get("name"), "price": s["extracted_price"], "link": s.get("link"), "major": _is_major(s.get("name"))}
            for s in (imm.get("product_results") or {}).get("stores", [])
            if s.get("extracted_price") and not PEER_MARKETS.search(s.get("name") or "")
        ]
        by_source.update({s["store"]: s["price"] for s in stores})
    return {
        **_band(list(by_source.values()), MIN_MARKET_PRICES), "basis": "new", "basis_text": "new retail",
        "n_shopping": sources, "n_stores": len(stores), "stores": stores, "stores_observed_at": observed_at,
        "reference_product": hero and {"title": hero.get("title"), "source": hero.get("source")},
    }


def _band(prices, min_n):
    """Median + robust spread. A "comparable" at under half or over double the others' median isn't the same product
    in the same condition (junk domains, parts, bulk/promo resellers): dropped before it can move the median or spread."""
    outliers = []
    if len(prices) >= min_n:
        mid = statistics.median(prices)
        outliers = sorted(p for p in prices if not mid / 2 <= p <= mid * 2)
        prices = [p for p in prices if mid / 2 <= p <= mid * 2]
    median = sigma = None
    if len(prices) >= min_n:
        median = statistics.median(prices)
        q1, _, q3 = statistics.quantiles(prices, n=4)
        sigma = round(min(max((q3 - q1) / 1.349, MIN_SPREAD * median), MAX_SPREAD * median), 2)
    return {"median": median, "sigma": sigma, "prices": sorted(prices), "outliers_dropped": outliers,
            "n_prices": len(prices), "min": min(prices, default=None), "max": max(prices, default=None)}


def _secondhand_market(listing, query, match, exclude=None):
    """Band for a used/refurbished listing: same-product eBay listings in the same condition bucket, one price per
    seller, the listing itself excluded. New retail is the wrong yardstick: normal used prices sit ~7 sigma below it."""
    bucket, is_match = listing["condition_bucket"], _matcher(match, exclude, secondhand=True)
    by_seller = {}
    for x in search("ebay", ebay_domain="ebay.com", _nkw=query).get("organic_results", []):
        price = (x.get("price") or {}).get("extracted")  # price ranges (multi-variant listings) have no single price
        if (price and x.get("product_id") != listing["id"] and _condition(x.get("condition")) == bucket
                and is_match(x.get("title"))):
            by_seller.setdefault((x.get("seller") or {}).get("username") or x.get("product_id"), price)
    return {**_band(list(by_seller.values()), MIN_USED_COMPARABLES), "basis": bucket,
            "basis_text": f"{bucket} units on eBay", "stores": [], "stores_observed_at": None, "reference_product": None}


def _snapshot(listing, market):
    """Append this run's prices to price_snapshots. A cache replay has the same observed_at -> skipped, not duplicated."""
    rows = [{"product_id": listing["id"], "source": f"{listing['marketplace']} listing",
             "price": listing["price"], "observed_at": listing["observed_at"]}]
    rows += [{"product_id": listing["id"], "source": s["store"], "price": s["price"], "observed_at": market["stores_observed_at"]}
             for s in market["stores"]]
    rows = [r for r in rows if r["price"] is not None]
    if rows:
        db.table("price_snapshots").upsert(rows, on_conflict="product_id,source,observed_at", ignore_duplicates=True).execute()


def _history(listing):
    """This listing's price in every earlier Clarity check, oldest first.
    serpapi_cache keeps one row per query (upserted), so trust_results is the only table with past prices."""
    rows = (
        db.table("trust_results").select("price:signals_json->listing->price, queries(created_at)")
        .eq("signals_json->listing->>marketplace", listing["marketplace"])
        .eq("signals_json->listing->>id", listing["id"])
        .execute().data
    )
    return sorted((r["queries"]["created_at"], r["price"]) for r in rows if r["price"] is not None)


def _price_trend(price, history):
    """Compare today's price with the listing's usual price. Returns (penalty reason | None, positive | None, signals).
    The baseline is the median of past prices, so a price that has stayed low for most checks is the norm, not a drop."""
    if not history:
        return None, None, {"n_checks": 0}
    baseline = statistics.median(p for _, p in history)
    drop = round((baseline - price) / baseline, 3)
    info = {"n_checks": len(history), "since": history[0][0][:10], "baseline": baseline, "drop": drop}
    if drop >= SUDDEN_DROP:
        return (f"Price just dropped {drop * 100:.0f}% — it was usually ${baseline:.2f} across {len(history)} earlier "
                f"checks since {info['since']}", None, info)
    if abs(drop) <= IN_LINE:
        return None, f"Priced about the same (${baseline:.2f}) across {len(history)} earlier checks since {info['since']}", info
    return None, None, info


def _listing_screenshot(product):
    """What a screenshot tells us: the product Lens identified, the price the user typed from the ad (never read from
    the image), and the shop if one was detected. No seller, returns, reviews or condition."""
    return {"marketplace": "screenshot", "id": product["listing_id"], "title": product["title"],
            "price": product.get("ad_price"), "seller": product.get("store_domain") or "unknown shop",
            "seller_is_platform": False, "condition": "unknown (screenshot)", "observed_at": None, "image": None}


def _photo(listing, image_url=None, image_bytes=None, emit=None):
    """Photo forensics + its signals. Returns (summary for signals_json, signals)."""
    r = asyncio.run(photo_forensics.run(image_url=image_url, image_bytes=image_bytes, emit=emit))
    sigs = photo_forensics.compute_photo_signals(r, listing)
    keep = ("engines_used", "engines_unavailable", "engine_stats", "total_matches", "total_display", "matches_by_class")
    # Top matches only (by class: maker, retailer, marketplace, ...): 400+ rows would bloat trust_results.
    return {**{k: r[k] for k in keep}, "image": image_url or "uploaded image", "top_matches": r["matches"][:24]}, sigs


def _store(domain):
    return asyncio.run(store_reputation.run(domain))


def _unavailable(names, why):
    return [{"signal": n, "status": "unavailable", "reason": why, "evidence": []} for n in names]


def run(product: dict, emit=None) -> dict:
    """product: {marketplace, listing_id, market_query, match, image?, store_domain?}. Returns {verdict, reasons, signals}.
    Screenshot input (resolve.resolve_screenshot): marketplace "screenshot", image_bytes, ad_price?, store_domain?.
    emit(event): optional live progress of the photo/store checks (the orchestrator streams it as SSE).
    store_domain: reputation runs only for domains that aren't a known retailer or marketplace."""
    shot = product["marketplace"] == "screenshot"
    if shot:
        listing = _listing_screenshot(product)
    else:
        listing = (_listing_amazon if product["marketplace"] == "amazon" else _listing_ebay)(product["listing_id"])
    # A screenshot's price is compared with new retail: the ad doesn't say new or used.
    listing["condition_bucket"] = bucket = "new" if shot else _condition(listing["condition"])

    # Evidence checks start now and run alongside the market check. A timeout or error makes them unavailable (0 points).
    # Demo products carry their pinned photo URL, so a re-fetched listing can't change the forensics cache key.
    started, pool = time.monotonic(), ThreadPoolExecutor(2)
    image, store_domain = product.get("image") or listing.get("image"), product.get("store_domain")
    photo_job = (pool.submit(_photo, listing, image_bytes=product["image_bytes"], emit=emit) if shot
                 else image and pool.submit(_photo, listing, image_url=image, emit=emit))
    store_job = store_domain and classify(f"https://{store_domain}/") == "unknown" and pool.submit(_store, store_domain)
    pool.shutdown(wait=False)  # never block on a slow check past its deadline
    # Hand-written for demo products; missing = unknown. No market query -> no market search, and the check is skipped.
    query, match = product.get("market_query"), product.get("match") or []
    if listing["price"] is None or not query:  # nothing to compare (no typed price, auction, no query): no market searches
        market = {**_band([], 1), "basis": "new", "basis_text": "new retail", "stores": [], "stores_observed_at": None,
                  "reference_product": None}
    elif bucket == "new":
        market = _market(query, match, product.get("exclude"))
    elif bucket == "parts":  # a for-parts unit has no fair "market price" to be anomalous against
        market = {**_band([], 1), "basis": "parts", "basis_text": "parts units", "stores": [], "stores_observed_at": None,
                  "reference_product": None}
    else:
        market = _secondhand_market(listing, query, match, product.get("exclude"))
    if not shot:
        _snapshot(listing, market)

    penalties, positives, waived = [], [], []
    first_party = listing["seller_is_platform"]

    def flag(check, text):
        if first_party and check in FIRST_PARTY_WAIVED:
            waived.append({"check": check, "reason": text})
        else:
            penalties.append({"check": check, "weight": WEIGHTS[check], "reason": text})
    price, median = listing["price"], market["median"]
    ratio = round(price / median, 2) if median and price is not None else None
    z = round((price - median) / market["sigma"], 2) if median and price is not None else None
    new = market["basis"] == "new"
    # "the new-retail median" vs "the usual price for used units"; the narrator repeats these words, so they must say which.
    usual = (f"the new-retail median of ${median:.2f}" if new
             else f"the usual price for {market['basis']} units (${median:.2f}, median of {market['n_prices']} eBay listings in the same condition)") if median else None
    spread = median and f"{market['basis_text']} prices for this product typically vary ±${market['sigma']:.2f}"
    # A screenshot doesn't say new or used, so its price is compared with new retail: every price reason says so.
    shot_note = ". The ad doesn't say new or used, so it's compared with new retail" if shot else ""

    if price is None:  # missing data is never a penalty
        positives.append("No price entered from the ad, so the price check was skipped (Clarity doesn't read prices "
                         "from screenshots)" if shot else
                         "This listing has no single fixed price (e.g. an auction), so the price check was skipped — "
                         "not counted either way")
    elif not query:
        positives.append("No market search is available for this product, so the price check was skipped — not counted")
    elif z is None and new:
        flag("market_unknown", f"Not enough comparable market prices ({market['n_prices']}) to verify the price")
    elif z is None:  # too few secondhand comparables: the check can't run, which is not evidence against the seller
        positives.append(f"Price check unavailable: only {market['n_prices']} comparable {market['basis_text']} "
                         f"(need {MIN_USED_COMPARABLES}) — not counted either way")
    elif z < -Z_TOO_CHEAP:
        flag("too_cheap", f"Price ${price:.2f} is {100 - ratio * 100:.0f}% below {usual}, "
                          f"{-z:.1f}x the normal spread ({spread}) — too good to be true{shot_note}")
    elif z > Z_TOO_EXPENSIVE:
        flag("too_expensive", f"Price ${price:.2f} is {ratio * 100 - 100:.0f}% above {usual}, "
                              f"{z:.1f}x the normal spread ({spread}){shot_note}")
    else:
        rel = "in line with" if abs(ratio - 1) <= IN_LINE else f"{abs(ratio - 1) * 100:.0f}% {'above' if ratio > 1 else 'below'}"
        compared = f" ({market['n_prices']} retail prices compared)" if new else ""
        positives.append(f"Price ${price:.2f} is {rel} {usual}{compared} — no anomaly{shot_note}")

    drop_reason, steady, history = _price_trend(price, [] if first_party or shot or price is None else _history(listing))
    if drop_reason:
        flag("sudden_drop", drop_reason)
    elif steady:
        positives.append(steady)

    others, seller_cheap = [], []
    if median:
        others = _seller_listings(listing, query, _matcher(match, product.get("exclude")))
        seller_cheap = [o for o in others if (o["price"] - median) / market["sigma"] < -Z_TOO_CHEAP]
    if seller_cheap:
        flag("seller_pattern", f"Seller '{listing['seller']}' has {len(seller_cheap)} other listing(s) of this product also "
                               f"priced implausibly low (${', $'.join(f'{o['price']:.2f}' for o in seller_cheap)})")

    if first_party:
        positives.append(f"Sold and shipped by {listing['seller']} itself — price checks don't count against it")
        positives += [f"Not counted (first-party listing): {w['reason']}" for w in waived]

    if shot:
        positives.append("A screenshot has no seller details, so the seller, returns and review checks don't apply")
    elif listing["marketplace"] == "amazon":
        if listing["reviews"] < MIN_REVIEWS:
            flag("few_reviews", f"Only {listing['reviews']} reviews on this listing")
        else:
            positives.append(f"{listing['reviews']:,} reviews on this listing")
        if listing["rating"] is not None and listing["rating"] < MIN_RATING:
            flag("low_rating", f"Low rating ({listing['rating']}/5)")
    else:
        if listing["seller_sales"] < MIN_SELLER_SALES:
            flag("unproven_seller", f"Seller '{listing['seller']}' has only {listing['seller_sales']} sales and {listing['seller_feedback']} feedback ({listing['seller_joined']})")
        else:
            positives.append(f"Seller has {listing['seller_sales']:,} sales")
        if not listing["returns_accepted"]:
            flag("no_returns", "Seller accepts no returns")

    def wait(job):
        return job.result(timeout=max(0.0, EVIDENCE_TIMEOUT - (time.monotonic() - started)))

    photo, photo_signals = None, _unavailable(photo_forensics.SIGNALS, "No listing photo to check (not counted)")
    if photo_job:
        try:
            photo, photo_signals = wait(photo_job)
        except Exception as e:
            photo_signals = _unavailable(photo_forensics.SIGNALS, f"Photo check unavailable ({type(e).__name__}); not counted")
    store, store_signals = None, []
    if store_job:
        try:
            store = wait(store_job)
            store_signals = store["signals"]
        except Exception as e:
            store_signals = _unavailable(("store_complaints", "no_web_footprint"),
                                         f"Store check unavailable ({type(e).__name__}); not counted")
    # Final state of each check, streamed whether it finished, failed or ran out of time.
    if photo_job:
        photo_forensics._emit(emit, type="forensics_signals", signals=photo_signals)
    if store_job:
        photo_forensics._emit(emit, type="store_reputation", domain=store_domain, signals=store_signals,
                              footprint=store and store["footprint"], complaint_hits=store and store["complaint_hits"],
                              ads=store and store["ads"])
    for sig in photo_signals + store_signals:
        if sig["status"] == "fired":
            flag(sig["signal"], sig["reason"])
    if photo and not photo["total_matches"]:
        positives.append("The photo search found no other copies of this image online")
    elif photo and not any(sig["status"] == "fired" for sig in photo_signals):
        positives.append(f"Photo checked across {len(photo['engines_used'])} search engine(s) "
                         f"({photo['total_display']} matches) — nothing unusual")

    score = max(0, 100 - sum(p["weight"] for p in penalties))
    fail = score < PASS_SCORE
    margin = (PASS_SCORE - score) / PASS_SCORE if fail else (score - PASS_SCORE) / (100 - PASS_SCORE)
    flags = [p["reason"] for p in penalties]
    top = max(penalties, key=lambda p: p["weight"], default=None)  # ties: the first flagged
    return {
        "verdict": "fail" if fail else "pass",
        # the flag that took the most points off, as a fact for the narrator (walk-aways only)
        "main_reason": fail and top and {"name": top["check"], "contribution": -top["weight"], "text": top["reason"]} or None,
        "confidence": round(0.5 + 0.45 * margin, 2),  # 0.50 at the line .. 0.95 at score 0 or 100
        "reasons": flags if fail else positives + flags,
        "signals": {
            "listing": listing,
            "market": market,
            "price_ratio": ratio, "price_z": z, "price_history": history,
            "seller_listings": {"found": others, "implausibly_cheap": seller_cheap},
            "score": score, "pass_score": PASS_SCORE,
            "penalties": penalties, "positives": positives, "first_party": first_party, "waived": waived,
            # Evidence for the narrator/UI without re-querying: matched thumbnails, domains, complaint snippets.
            "photo": {"forensics": photo, "signals": photo_signals},
            "store": {"domain": store_domain, "reputation": store, "signals": store_signals,
                      "applies": bool(store_job)},
        },
    }


if __name__ == "__main__":
    import json
    import sys

    from itertools import combinations

    from demo_products import DEMO_CHIPS, DEMO_PRODUCTS

    # Weighted score must reproduce the old flag-count rule for every combination of checks.
    for n in range(len(WEIGHTS) + 1):
        for combo in combinations(WEIGHTS, n):
            old_fail = "too_cheap" in combo or len(combo) >= 2
            assert (100 - sum(WEIGHTS[c] for c in combo) < PASS_SCORE) == old_fail, combo

    assert "few_reviews" not in FIRST_PARTY_WAIVED and "low_rating" not in FIRST_PARTY_WAIVED

    # Condition buckets (eBay condition strings seen in real data; Amazon's are set in _listing_amazon).
    for text, want in [("Brand New", "new"), ("New", "new"), ("Pre-Owned", "used"), ("Used", "used"), ("Open box", "used"),
                       ("New other (see details)", "used"), ("Good - Refurbished", "refurbished"), ("Parts Only", "parts")]:
        assert _condition(text) == want, (text, _condition(text))
    assert _matcher(["airpods pro"], secondhand=True)("AirPods Pro 2 Pre-Owned") and not _matcher(["airpods pro"])("AirPods Pro 2 Pre-Owned")

    # Price history: none -> neutral; steady -> positive; recent cut -> flag; long-standing low price -> not a drop.
    h = lambda *ps: [(f"2026-09-{i + 10:02}", p) for i, p in enumerate(ps)]
    assert _price_trend(300, []) == (None, None, {"n_checks": 0})
    assert _price_trend(300, h(300, 300))[1] and not _price_trend(300, h(300, 300))[0]
    assert _price_trend(300, h(500, 500, 500))[0]
    assert not _price_trend(300, h(500, 300, 300, 300))[0]

    names = [a for a in sys.argv[1:] if not a.startswith("-")] or list(DEMO_CHIPS)
    mismatches = []
    for name in names:
        p = DEMO_PRODUCTS[name]
        r = run(p)
        s = r["signals"]
        print(f"\n=== {name}  ->  {r['verdict'].upper()} ({r['confidence']:.0%})  (expected {p['expect_trust']})  score {s['score']}")
        print(f"listing: {s['listing']['title'][:70]} | ${s['listing']['price']} | seller {s['listing']['seller']}")
        m = s["market"]
        print(f"reference product: {m['reference_product']}")
        print(f"band: {m['basis_text']}")
        print(f"market: median ${m['median']} from {m['n_prices']} prices ({m.get('n_shopping', 0)} shopping + {m.get('n_stores', 0)} stores), range ${m['min']}–${m['max']} | ratio {s['price_ratio']} | sigma ${m['sigma']} | z {s['price_z']}")
        for reason in r["reasons"]:
            print(f"  - {reason}")
        if "-v" in sys.argv:
            print(json.dumps(s, indent=2, default=str))
        if r["verdict"] != p["expect_trust"]:
            mismatches.append(name)
    print("\nMISMATCHES vs Phase 0:", mismatches or "none")
    sys.exit(1 if mismatches else 0)
