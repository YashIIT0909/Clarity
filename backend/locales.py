"""Markets Clarity checks against: "us" (amazon.com, eBay, US retailers, $) and "in" (amazon.in, Indian retailers, ₹).

The link decides the market (amazon.in -> in; amazon.com, eBay -> us); typed text and screenshots carry no country, so
the caller passes one (the web app defaults to India). US settings are exactly the params Clarity always sent: every
US cache key, pinned demo and cached narration stays valid. India adds its own params (amazon_domain, gl, geo, ...).

Run: .venv/bin/python locales.py   (self-check, no searches)
"""
import re

DEFAULT = "us"  # for code paths with no market given (scripts, tests, pinned demos); the web app passes "in"

MARKETS = {
    "us": {
        "name": "US", "country": "US", "currency": "USD", "symbol": "$",
        "amazon": {},                        # amazon.com is SerpApi's default: no amazon_domain param (US cache keys)
        "gl": "us", "geo": "US",
        "trends_geo": None,                  # demand Trends never sent a geo (worldwide); kept so US keys don't change
        "ads_region": "2840",                # Google Ads geo target: United States
        "ebay": True,
        "whole_prices_only": False,
        "first_party": {"Amazon.com"},       # Shipper/Seller as amazon_product gives it: Amazon sells and ships
        # Known retailers. Their product pages list many stores; only these (or their marketplace sellers,
        # "Walmart - X") may supply the reference product whose store list joins the market sample.
        "retailers": {
            "best buy", "walmart", "target", "gamestop", "amazon.com", "costco", "home depot", "the home depot",
            "lowe's", "b&h photo-video-audio", "sam's club", "bj's wholesale club", "apple", "sony", "nintendo",
            "tractor supply company", "ace hardware", "staples", "office depot", "kohl's", "macy's",
        },
        # Peer-to-peer marketplaces: the same kind of listing being judged, so never a retail price reference.
        "peers": r"mercari|poshmark|ebay|bonanza|jawa|facebook|offerup|shein|whatnot",
    },
    "in": {
        "name": "India", "country": "IN", "currency": "INR", "symbol": "₹",
        "amazon": {"amazon_domain": "amazon.in"},
        "gl": "in", "geo": "IN",
        "trends_geo": "IN",
        "ads_region": "2356",                # Google Ads geo target: India
        "ebay": False,                       # ebay.in closed in 2017: no secondhand band, no seller-pattern search
        # Indian shops price in whole rupees. Paise (₹24,317.31) mark a foreign shop whose price Google converted to ₹
        # (dakauf.eu, papita.co, mygsm.me, slickwraps in phase0/india_probe): not the Indian market, so dropped.
        "whole_prices_only": True,
        # Only Amazon itself. On amazon.in most items are sold by Amazon-affiliated sellers (Clicktech, Appario,
        # Cocoblu): their prices are checked like any seller's.
        "first_party": {"Amazon", "Amazon.in"},
        # Indian retailers seen in Google Shopping India (phase0/india_probe, 2026-10-09), plus makers' own stores.
        "retailers": {
            "amazon.in", "flipkart", "croma", "reliance digital", "vijay sales", "tata cliq", "tata cliq luxury",
            "jiomart", "myntra", "ajio", "ajio.com", "apple", "sony", "samsung", "nintendo",
        },
        # Not a reference for the Indian retail price: peer and refurbished marketplaces (Cashify sells refurbished
        # units), and cross-border import shops, which list imported units at up to 2x Indian retail in whole rupees
        # (shoptheworld.in ₹49,857 and wafuu.com ₹44,700 for a ₹19,989 XM5, phase0/india_probe). Hand list: add an
        # importer when one shows up.
        "peers": r"olx|quikr|cashify|ebay|facebook|shopsy|meesho|shoptheworld|wafuu|ubuy|desertcart",
    },
}


def get(market):
    return MARKETS[market or DEFAULT]


def from_link(text):
    """The market a link names: "in" for amazon.in, "us" for amazon.com and eBay, None when the text has no link
    (typed text: the caller's market applies). Other Amazon sites are rejected by resolve, not guessed."""
    if re.search(r"amazon\.in\b", text or "", re.I):
        return "in"
    if re.search(r"amazon\.com\b|ebay\.[a-z.]+/itm/", text or "", re.I):
        return "us"
    return None


def _indian_grouping(n):
    """1234567 -> '12,34,567': the last three digits, then pairs."""
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    pairs = []
    while len(head) > 2:
        head, pairs = head[:-2], [head[-2:]] + pairs
    return ",".join([head] + pairs + [tail])


def money(amount, market=None):
    """'$298.00' exactly as Clarity always wrote it (US text feeds cached narrations), or whole rupees with Indian
    digit grouping: '₹19,989', '₹1,24,990'."""
    if (market or DEFAULT) == "us":
        return f"${amount:.2f}"
    rupees = int(amount + 0.5) if amount >= 0 else -int(-amount + 0.5)
    return f"{'-' if rupees < 0 else ''}₹{_indian_grouping(abs(rupees))}"


def all_retailers():
    """Every market's retailers: domain classes are global (a photo on flipkart.com is a retailer's in any market)."""
    return set().union(*(m["retailers"] for m in MARKETS.values()))


if __name__ == "__main__":
    assert money(298) == "$298.00" and money(36.24, "us") == "$36.24" and money(1234.5) == "$1234.50"  # as before
    assert money(19989, "in") == "₹19,989" and money(124990, "in") == "₹1,24,990" and money(24317.31, "in") == "₹24,317"
    assert money(999, "in") == "₹999" and money(1000, "in") == "₹1,000" and money(12345678, "in") == "₹1,23,45,678"
    assert money(0.5, "in") == "₹1" and money(-2500, "in") == "-₹2,500"
    assert from_link("https://www.amazon.in/dp/B09XS7JWHH") == "in" and from_link("amazon.in/Sony/dp/B09XS7JWHH?th=1") == "in"
    assert from_link("https://www.amazon.com/dp/B09XS7JWHH") == "us" and from_link("https://www.ebay.com/itm/178516729136") == "us"
    assert from_link("sony wh-1000xm5") is None and from_link("https://www.amazon.co.uk/dp/B09XS7JWHH") is None
    assert get(None) is MARKETS["us"] and get("in")["symbol"] == "₹"
    assert {"flipkart", "best buy"} <= all_retailers()
    print("LOCALES SELF-CHECK PASSED")
