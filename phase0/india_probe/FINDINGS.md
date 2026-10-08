# India probe: findings (2026-10-09)

`probe.py`, 6 calls through `serpapi.search` (cached), 5 searches spent (159 -> 154). Raw responses in `out/`.
Product: Sony WH-1000XM5, to line up with the US `sony-xm5` demo.

## What works

| # | Call | Result |
|---|---|---|
| 1 | `amazon` k=…, `amazon_domain=amazon.in` | amazon.in results, `price` "₹19,989", `extracted_price` 19989 (plain number, ₹) |
| 2 | `amazon_product` asin, `amazon_domain=amazon.in` | Works. ₹ price, rating 5.0, 17,252 reviews, 15 photos, brand "Visit the Sony Store" (the existing regex strips it) |
| 3 | `google_shopping` gl=in | 40 results, all in ₹, many with an immersive token |
| 4 | `google_immersive_product` | 3 stores, each with a link: **Amazon.in ₹19,989, Flipkart ₹19,989, ajio.com ₹23,664** |
| 5 | `google_trends` TIMESERIES geo=IN | 262 weeks; Indian interest is low (5-11) with spikes (95), so momentum will be noisy |
| 6 | `google_finance` VOLTAS:NSE | 125 points, currency INR: NSE tickers work |

## What the India build must handle

1. **Same ASIN in both countries.** B09XS7JWHH is the XM5 on amazon.com *and* amazon.in. `resolve.resolve` maps any
   text containing a demo's listing id to that demo, so an amazon.in link would become the **US** `sony-xm5` demo.
   The demo lookup must also match the domain.
2. **Seller fields differ.** amazon.in gives `purchase_options.single_offer.features` with separate `ships_from`
   ("Amazon") and `sold_by` ("Clicktech Retail Private Ltd"). `trust._listing_amazon` reads `shipper_seller`, so on
   amazon.in the seller becomes "unknown" and the first-party waiver never applies. Decide what first-party means in
   India: Amazon's affiliated sellers (Clicktech, Appario, Cocoblu) sell most items; "Ships from Amazon" alone isn't it.
3. **Google Shopping India is noisy.** Within the first 20 results:
   - the earbuds (WF-1000XM5) next to the headphones (WH-1000XM5): demo `match` rules must name "wh";
   - accessories (XtremeSkins skins at ₹1,700): the existing ACCESSORY regex catches "skin";
   - foreign shops priced in converted ₹ (dakauf.eu, papita.co, wafuu.com at ₹44,600): the outlier cut (over 2x the
     median) drops some, not all; consider dropping non-Indian domains;
   - Hindi titles ("सोनी WH-1000XM5 …"): the model number still matches;
   - Cashify (refurbished marketplace): treat as peer/secondhand, not retail.
4. **Indian retailers seen:** Amazon.in, Flipkart, Myntra, TATA CLiQ (Luxury), Reliance Digital, ajio.com, Cashify.
   Flipkart arrives as a store with a link, so it can be "where to buy instead" even though Flipkart links can't be
   checked (no SerpApi Flipkart engine).
5. **Prices are integers in ₹ at 5 digits.** Money formatting needs Indian grouping (₹19,989; ₹1,24,990 for lakhs).
   No conversion is needed: SerpApi already returns ₹.

## Quick read on the XM5 in India

amazon.in ₹19,989 and Flipkart ₹19,989 sit at the bottom of the clean retail prices (₹19,989-₹26,999), so it would
likely get Buy Now, with no cheaper trusted store. A Wait demo for India needs a listing priced above the Indian market,
as with `honeywell-ebay` in the US.
