# Pre-verified demo set (Phase 0). `match` = regexes a Shopping title must all match to count as the same product.
# `image` = the listing's main photo (Amazon product_results.thumbnails[0], eBay media[0].image[-1]) for photo forensics.
# Not AirPods Pro 2: Max/Beats, other generations, and case-only listings ("Charging Case" with no earbuds/"with").
AIRPODS_EXCLUDE = (r"max|beats|usado|pro ?3|3rd|third gen|\b1st\b|first gen"
                   r"|^(?!.*\b(with\b|w/|and\b|earbuds|earphones|headphones|buds|headset)).*charging case")

DEMO_PRODUCTS = {
    "airpods-ebay": {
        "label": "AirPods Pro 2 on eBay", "url": "https://www.ebay.com/itm/178516729136",
        "image": "https://i.ebayimg.com/images/g/tyMAAeSwbUdqsYQI/s-l960.jpg",
        "marketplace": "ebay", "listing_id": "178516729136",
        "market_query": "AirPods Pro 2", "match": ["airpods pro"], "exclude": AIRPODS_EXCLUDE,
        "brand": "Apple", "category": "audio", "expect_trust": "fail",
        "trend_query": "airpods pro 2", "expect_timing": None,
    },
    "airpods-openbox": {
        "label": "AirPods Pro 2 'open box' on eBay", "url": "https://www.ebay.com/itm/287595183543",
        "image": "https://i.ebayimg.com/images/g/6Z4AAeSw3TxqrfW6/s-l960.jpg",
        "marketplace": "ebay", "listing_id": "287595183543",
        "market_query": "AirPods Pro 2", "match": ["airpods pro"], "exclude": AIRPODS_EXCLUDE,
        "brand": "Apple", "category": "audio", "expect_trust": "fail",
        "trend_query": "airpods pro 2", "expect_timing": None,
        # Photo-forensics walk-away (Prompt 2 probe, 2026-09-29): described as used ("light signs of use ... scuffs
        # and/or scratches") but its only photo is Apple's studio render (Bing: on apple.com/es + apple.com.cn);
        # seller cr072572 joined Sep 2026 with 1 rating, lists 10 units of a "used" item, no returns. Pinned fixture.
        "note": "Stock render on a 'used' listing, new seller, 10 units, no returns",
    },
    "sony-xm5": {
        "label": "Sony WH-1000XM5", "url": "https://www.amazon.com/dp/B09XS7JWHH",
        "image": "https://m.media-amazon.com/images/I/61O3iMlnJIL._AC_SL1500_.jpg",
        "marketplace": "amazon", "listing_id": "B09XS7JWHH",
        "market_query": "sony wh-1000xm5", "match": ["1000xm5"], "exclude": r"\+|bundle|with ",
        "brand": "Sony", "category": "audio", "expect_trust": "pass",
        # "buy" for the current demo set; the wait slot is open until Successor radar (open decision #2).
        "trend_query": "sony wh-1000xm5", "expect_timing": "buy",
    },
    "honeywell-heater": {
        "label": "Honeywell 360° heater", "url": "https://www.amazon.com/dp/B00VV156LA",
        "image": "https://m.media-amazon.com/images/I/61H3SpmyIIL._AC_SL1200_.jpg",
        "marketplace": "amazon", "listing_id": "B00VV156LA",
        "market_query": "Honeywell HHF360V heater", "match": [r"360 surround|hhf360"], "exclude": r"digital",
        "brand": "Honeywell", "category": "heating", "expect_trust": "pass",
        "trend_query": "space heater", "expect_timing": "buy",
    },
    "switch2": {
        "label": "Nintendo Switch 2", "url": "https://www.amazon.com/dp/B0F3GWXLTS",
        "image": "https://m.media-amazon.com/images/I/714-Fh3ngmL._SL1500_.jpg",
        "marketplace": "amazon", "listing_id": "B0F3GWXLTS",
        "market_query": "Nintendo Switch 2 console", "match": ["switch 2"], "exclude": r"bundle|\+|with |mario|pok|zelda|kong",
        "brand": "Nintendo", "category": "gaming", "expect_trust": "pass",
        "trend_query": "nintendo switch 2", "expect_timing": "buy",
    },
    "switch2-pokemon-bundle": {
        "label": "Switch 2 + Pokémon bundle", "url": "https://www.amazon.com/dp/B0GRV1RWMY",
        "image": "https://m.media-amazon.com/images/I/715+ajJYKbL._SL1500_.jpg",
        "marketplace": "amazon", "listing_id": "B0GRV1RWMY",
        "market_query": "Nintendo Switch 2 console", "match": ["switch 2", "pok"],
        "brand": "Nintendo", "category": "gaming", "expect_trust": None,
        "trend_query": "nintendo switch 2", "expect_timing": None,
        # Not a demo (2026-09-29, open decision #1 resolved): it never failed for a real reason, and on fresh data it
        # failed only because Shopping showed no Pokémon bundles (market_unknown). Walk-away is covered by airpods-ebay
        # (price) and airpods-openbox (photo + seller + returns). Excluded from chips, assertions and pinning.
        "demo": False,
    },
}

# The demo set: chips in the UI, self-test defaults, pinned fixtures.
DEMO_CHIPS = {k: p for k, p in DEMO_PRODUCTS.items() if p.get("demo", True)}
# Pre-verified screenshot chips (synthetic ads, phase0/photo_probe/screens/make_ads.py). The frontend serves the same
# file from public/demo/ and uploads it byte-for-byte, so its Lens results come from the cache.
DEMO_SCREENSHOTS = {
    "screenshot-sony-59": {"file": "../frontend/public/demo/ad_sony_xm5_59.png", "price": 59.0,
                           "expect_trust": "fail", "expect_timing": None},
}

# Demo-only brand -> Google Finance ticker. Not general brand resolution.
BRAND_TICKERS = {
    "Apple": "AAPL:NASDAQ",
    "Sony": "SONY:NYSE",
    "Honeywell": "HELE:NASDAQ",  # Honeywell heaters are made under licence by Helen of Troy
    "Nintendo": "NTDOY:OTCMKTS",
}
