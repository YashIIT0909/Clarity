"""Turn the single user input (URL or free text) into a product dict the agents understand."""
import re

import locales
import photo_forensics
import serpapi
from demo_products import DEMO_PRODUCTS
from domains import classify, is_listing_page, registered_domain
from lifecycle import search_term
from serpapi import search

AMAZON_ASIN = re.compile(r"amazon\.[a-z.]+/(?:.*/)?(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", re.I)
EBAY_ITEM = re.compile(r"ebay\.[a-z.]+/itm/(?:[^/?#]+/)?(\d{9,15})", re.I)


class ResolveError(ValueError):
    pass


def _short(title, words):
    title = re.split(r"\s[-–|,(]|,", title)[0]
    return " ".join(title.split()[:words])


def _generic(marketplace, listing_id, market=None):
    if marketplace == "amazon":
        p = search("amazon_product", asin=listing_id, **locales.get(market)["amazon"])["product_results"]
        brand = re.sub(r"^(Visit the |Brand: )|( Store)$", "", p.get("brand") or "").strip()
    else:
        p = search("ebay_product", product_id=listing_id)["product_results"]
        brand = ""
    title = p["title"]
    brand = brand or title.split()[0]
    # Brand + model ("Motorola MA1") when the title has a clear model identifier; else the first title words.
    term = search_term(title, brand)
    return {
        "marketplace": marketplace, "market": market or locales.DEFAULT, "listing_id": listing_id,
        "market_query": term or _short(title, 6), "match": [], "trend_query": term or _short(title, 3),
        "brand": brand, "label": _short(title, 6),
    }


MIN_CONFIRMING = 2  # visual matches whose titles name Lens's identified product (its first two words: brand + model)


def _key_words(title):
    """Brand + model: the first two words of Lens's product name."""
    return [w.lower() for w in title.split()[:2]]


def _identify(lens):
    """(title, confirming matches): Google's product name (related_content), confirmed by the visual matches.
    ponytail: "confirmed" = 2+ match titles contain the name's first two words; it can't tell generations apart
    (a Switch 2 photo is named "Nintendo Switch"). Upgrade: compare model numbers when the name has one."""
    names = [r["query"] for r in lens.get("related_content") or [] if r.get("query")]
    if not names:
        return None, []
    key = _key_words(names[0])
    confirming = [m for m in lens.get("visual_matches") or [] if all(w in (m.get("title") or "").lower() for w in key)]
    return (names[0] if len(confirming) >= MIN_CONFIRMING else None), confirming


def _advertised_store(photo_hash, matches):
    """The shop the screenshot advertises: its image pixel-matches a product page on exactly one domain that isn't a
    maker, retailer, marketplace, press or forum. Anything else (none, or several) -> None: never guess."""
    shops = [{**m, "domain": registered_domain(m["link"]), "url": m["link"]} for m in matches
             if m.get("link") and classify(m["link"]) == "unknown" and is_listing_page(m["link"])]
    same = photo_forensics._same_photo(photo_hash, shops) if photo_hash else []
    domains = {m["domain"] for m in same}
    return (domains.pop() if len(domains) == 1 else None), same


def resolve_screenshot(data: bytes, ad_price: float | None = None, market: str | None = None) -> tuple[str, dict]:
    """Uploaded screenshot -> product dict for the agents. Lens identifies the product; the price only ever comes from
    ad_price, which the user typed (no OCR). Cached on the image's sha256, so a re-run makes no SerpApi call or upload.
    market: the screenshot carries no country, so the caller says which market to price it in (web app: India)."""
    ident = photo_forensics.identity(data=data)
    image_id = None

    def lens(kind):
        nonlocal image_id
        key = {"image": ident, "type": kind}
        if hit := serpapi.cached("google_lens", key):
            return hit
        image_id = image_id or photo_forensics._upload(data)
        return serpapi.search("google_lens", key, image_id=image_id, type=kind)

    title, confirming, tried = None, [], []
    for kind in ("products", "all"):  # "all" only if "products" can't name it
        r = lens(kind)
        title, confirming = _identify(r)
        tried.append(kind)
        if title:
            break
    if not title:
        raise ResolveError("Couldn't identify the product in this screenshot with confidence. Try a screenshot where "
                           "the product is clearly visible, or paste a link to the listing instead.")
    store, store_evidence = _advertised_store(photo_forensics._photo_hash(data=data), r.get("visual_matches") or [])
    priced = [{"title": m.get("title"), "source": m.get("source"), "link": m.get("link"),
               "price": (m.get("price") or {}).get("extracted_value")} for m in r.get("visual_matches") or [] if m.get("price")]
    match = [re.escape(w) for w in _key_words(title)]
    # India: the clean model term ("Sony WH-1000XM5"), as links use. Indian Shopping returns other products for Lens's
    # long US-spelled name ("… Noise Canceling Wireless Headphones": Beats, a WHOOP band, Nike shoes; 2026-10-09).
    # US keeps the first six words it always used (its pinned screenshot demo is cached under that query).
    clean = market == "in" and search_term(title, title.split()[0])
    query, trend = (clean, clean) if clean else (_short(title, 6), _short(title, 3))
    if ad_price is not None:
        # A price we can't check against anything is not a pass. "AirPods Pro" (no generation) finds only resale
        # listings on Shopping: stop cleanly rather than let an unverified $49 through. The market is cached for trust.
        import trust  # lazy: trust -> photo_forensics; resolve is only imported by the API
        if not trust._market(query, match, market=market)["median"]:
            raise ResolveError(f"This looks like “{title}”, but that's too general to check the {locales.money(ad_price, market)} price "
                               "against: no retail prices were found for exactly that product. Paste a link to the "
                               "listing, or use a screenshot that shows the exact model.")
    product = {
        "marketplace": "screenshot", "market": market or locales.DEFAULT, "listing_id": ident[:16], "image_bytes": data, "ad_price": ad_price,
        "store_domain": store, "title": title, "label": _short(title, 6),
        # The words that confirmed the identification also filter the market: "AirPods Pro" must not be priced
        # against AirPods 4 (the first run did exactly that: Shopping's reference product was "AirPods 4").
        "market_query": query, "match": match,
        "trend_query": trend, "brand": title.split()[0], "category": None,
        "lens": {"identified_as": title, "lens_types_used": tried, "confirming_matches": len(confirming),
                 "priced_matches": priced[:8], "store": store,
                 "store_note": None if store else "Couldn't tell which shop this is from the image, so no shop check ran",
                 "store_evidence": [{k: m.get(k) for k in ("domain", "url", "photo_distance")} for m in store_evidence]},
    }
    return product["label"], product


def resolve(text: str, market: str | None = None) -> tuple[str, dict]:
    """Returns (label, product). The link decides the market (amazon.in -> India; amazon.com, eBay -> US); typed text
    uses `market` (the web app sends India; scripts and tests default to US). Demo listings map to their pre-verified
    config, only in their own market: one ASIN can be on amazon.com and amazon.in."""
    text = text.strip()
    if not text:
        raise ResolveError("Paste a product link or describe the product.")
    if text in DEMO_PRODUCTS:
        return text, DEMO_PRODUCTS[text]
    asin = AMAZON_ASIN.search(text)
    if asin and not re.search(r"amazon\.(com|in)\b", text, re.I):  # amazon.co.uk & co: never priced in the wrong store
        raise ResolveError("Only amazon.com and amazon.in product pages are supported, plus eBay listings.")
    market = locales.from_link(text) or market or locales.DEFAULT
    for key, p in DEMO_PRODUCTS.items():
        if p["listing_id"] in text and p.get("market", "us") == market:
            return key, p

    if asin:
        p = _generic("amazon", asin.group(1).upper(), market)
    elif m := EBAY_ITEM.search(text):
        p = _generic("ebay", m.group(1), "us")  # eBay has no Indian site: an eBay listing is checked in the US market
    elif re.match(r"https?://", text):
        raise ResolveError("Only Amazon product pages (…/dp/ASIN) and eBay listings (…/itm/ID) are supported.")
    else:
        hit = next((r for r in search("amazon", k=text, **locales.get(market)["amazon"]).get("organic_results", [])
                    if r.get("asin")), None)
        if not hit:
            raise ResolveError(f"No Amazon listing found for “{text}”"
                               f"{' on amazon.in' if market == 'in' else ''}.")
        p = _generic("amazon", hit["asin"], market)
    return p["label"], p


if __name__ == "__main__":
    for url, want in [
        ("https://www.amazon.com/Sony-WH-1000XM5/dp/B09XS7JWHH/ref=sr_1_1", "B09XS7JWHH"),
        ("https://amazon.com/gp/product/b00vv156la?th=1", "B00VV156LA"),
        ("https://www.ebay.com/itm/178516729136?hash=x", "178516729136"),
        ("https://www.ebay.com/itm/apple-airpods/178516729136", "178516729136"),
    ]:
        m = AMAZON_ASIN.search(url) or EBAY_ITEM.search(url)
        assert m and m.group(1).upper() == want, url
    assert resolve("https://www.ebay.com/itm/178516729136")[0] == "airpods-ebay"
    assert resolve("sony-xm5")[0] == "sony-xm5"
    for bad in ["", "https://walmart.com/ip/123"]:
        try:
            resolve(bad)
            raise AssertionError(bad)
        except ResolveError:
            pass
    print("resolve checks passed")
