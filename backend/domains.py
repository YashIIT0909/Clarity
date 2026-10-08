"""Who is behind a web domain, for photo-forensics matches. Maintained by hand; retailers come from trust.py.

Classes: manufacturer | known_retailer | marketplace_listing | review_or_press | forum_or_scam_report | unknown.
Matching is on the registered domain's name label ("sony" in sony.co.uk), so regional sites need no extra entries.
"""
import re
from urllib.parse import urlparse

# Makers of our demo products (and their licensors / licensees).
MANUFACTURERS = {
    "apple",                                   # AirPods
    "sony", "sony-mea",                        # WH-1000XM5 (sony-mea = Sony Middle East & Africa)
    "honeywell", "honeywellstore", "kaz", "helenoftroy",  # Honeywell heaters are made under licence by Helen of Troy (Kaz)
    "nintendo", "pokemon",                     # Switch 2; the Pokemon Company for the bundle's game
}
# Peer-to-peer / third-party-seller marketplaces: a match here is someone else's listing.
MARKETPLACES = {
    "ebay", "mercari", "poshmark", "offerup", "craigslist", "etsy", "aliexpress", "alibaba", "temu", "shein", "wish",
    "depop", "vinted", "wallapop", "olx", "avito", "kijiji", "carousell", "shopee", "lazada", "bonanza", "whatnot",
    "jawa", "swappa", "gumtree", "marktplaats", "leboncoin", "ricardo", "fril", "rakuma", "dhgate", "stockx",
    "mercadolibre", "mercadolivre", "ozon", "daraz", "basalam", "reverb", "indiamart", "markaz",
}
# Hosts inside a larger domain that are marketplaces (yahoo.co.jp is mostly not).
MARKETPLACE_HOSTS = re.compile(r"^(auctions\.yahoo\.|page\.auctions\.yahoo\.|.*facebook\.com/marketplace)")
FORUMS_AND_SCAM_REPORTS = {
    "reddit", "quora", "trustpilot", "scamadviser", "sitejabber", "bbb", "ripoffreport", "pissedconsumer",
    "complaintsboard", "scam-detector", "scamwatch", "reviews", "resellerratings", "stackexchange",
    "hotukdeals",  # deal-community forum. Not tiktok: social video, 36 of 63 "forum" hits in the probe, all noise
}
REVIEW_OR_PRESS = {
    "youtube", "rtings", "cnet", "theverge", "techradar", "tomsguide", "wired", "engadget", "pcmag", "whathifi",
    "soundguys", "9to5mac", "9to5toys", "macrumors", "appleinsider", "gsmarena", "ign", "gamespot", "polygon",
    "nintendolife", "gonintendo", "bestreviews", "bobvila", "nytimes", "forbes", "businessinsider", "digitaltrends",
    "androidcentral", "androidpolice", "zdnet", "headfonia", "versus", "consumerreports", "goodhousekeeping",
    "impress", "laptopmag", "lifehacker", "popularmechanics", "menshealth", "esquire",
}

# Search-bait "ask an expert" Q&A pages: SEO landing pages built on a search query, not an owner's thread. Excluded from
# owner receipts forums. ponytail: hand list; add a site when one shows up in google_forums results.
SEARCH_BAIT = {"justanswer", "fixya", "answers", "ehow"}


def is_search_bait(url):
    return registered_domain(url).split(".")[0] in SEARCH_BAIT


# One seller's listing, not a category/search/catalog page that aggregates many (eBay /b/ and /p/N, MercadoLibre /p,
# StockX, AliExpress /wholesale/). Shapes taken from the probe's real marketplace URLs.
LISTING_PAGE = re.compile(r"/itm/\d|/items?/|/listing/|/auction/|/proddetail/|/products?/|-i\.\d+\.\d+|/view/|/p/[^/?]*-\d{5,}", re.I)


def is_listing_page(url):
    return bool(LISTING_PAGE.search(urlparse(url).path or ""))


def registered_domain(url):
    """www.bestbuy.com -> bestbuy.com, amazon.co.uk -> amazon.co.uk. ponytail: two-label rule + common 2nd-level
    country suffixes, not the full public-suffix list; add tldextract if odd suffixes start misclassifying."""
    parts = (urlparse(url or "").hostname or "").removeprefix("www.").split(".")
    n = 3 if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "org", "net", "ne", "or", "ac"} else 2
    return ".".join(parts[-n:])


def _is_retailer(label):
    import locales  # every market's retailers: a photo on flipkart.com is a retailer's in any market
    keys = {re.sub(r"[^a-z]", "", n.removesuffix(".com")) for n in locales.all_retailers()}
    # "bhphotovideo" ~ "b&h photo-video-audio"; the 5-char floor stops "mac" (mac.bid) matching "macy's"
    return label in keys or (len(label) >= 5 and any(k.startswith(label) for k in keys))


def classify(url):
    domain = registered_domain(url)
    label = domain.split(".")[0]
    host_path = (urlparse(url).hostname or "") + (urlparse(url).path or "")
    if label in MANUFACTURERS:
        return "manufacturer"
    if label in MARKETPLACES or MARKETPLACE_HOSTS.match(host_path):
        return "marketplace_listing"
    if _is_retailer(label):
        return "known_retailer"
    if label in FORUMS_AND_SCAM_REPORTS:
        return "forum_or_scam_report"
    if label in REVIEW_OR_PRESS:
        return "review_or_press"
    return "unknown"


if __name__ == "__main__":
    for url, want in [
        ("https://www.sony.co.uk/electronics/x", "manufacturer"), ("https://www.apple.com/es/shop/x", "manufacturer"),
        ("https://www.ebay.com/itm/123", "marketplace_listing"), ("https://jp.mercari.com/item/m1", "marketplace_listing"),
        ("https://auctions.yahoo.co.jp/jp/auction/x1", "marketplace_listing"), ("https://news.yahoo.co.jp/a", "unknown"),
        ("https://www.facebook.com/marketplace/riga/x", "marketplace_listing"), ("https://www.facebook.com/groups/1/posts/2", "unknown"),
        ("https://www.bestbuy.com/product/x", "known_retailer"), ("https://www.amazon.co.uk/dp/x", "known_retailer"),
        ("https://www.bhphotovideo.com/c/x", "known_retailer"), ("https://mac.bid/x", "unknown"),
        ("https://www.reddit.com/r/x", "forum_or_scam_report"), ("https://www.youtube.com/watch?v=1", "review_or_press"),
        ("https://abri-voyageur.ca/x", "unknown"),
    ]:
        assert classify(url) == want, (url, classify(url), want)
    for url, want in [
        ("https://www.ebay.com/itm/376188946778", True), ("https://jp.mercari.com/item/m38277921433", True),
        ("https://offerup.com/item/detail/515382f1", True), ("https://www.carousell.sg/p/airpods-pro-2nd-generation-1349876543", True),
        ("https://shopee.vn/Tai-nghe-AirPods-i.116309859.18866092501", True), ("https://auctions.yahoo.co.jp/jp/auction/x1154668415", True),
        ("https://www.ozon.ru/product/sony-naushniki-1234567/", True), ("https://reverb.com/item/12345-sony-wh-1000xm5", True),
        ("https://www.ebay.com/b/Apple-Headphones/112529/bn_886136", False), ("https://www.ebay.com/p/1234567890", False),
        ("https://www.ebay.com/shop/xm5-sony?_nkw=xm5+sony", False), ("https://www.aliexpress.com/wholesale/sony-headset.html", False),
        ("https://stockx.com/sony-wireless-headphones-wh-1000xm5-b-black", False), ("https://reverb.com/p/sony-wh-1000xm5", False),
        ("https://articulo.mercadolibre.com.mx/calentador-honeywell-360-color-negro/p", False),
    ]:
        assert is_listing_page(url) == want, (url, want)
    assert is_search_bait("https://www.justanswer.com/electronics/x.html") and not is_search_bait("https://learn.microsoft.com/x")
    print("domains ok")
