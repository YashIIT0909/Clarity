"""Where to buy instead: the cheapest trusted store selling the same product, from the retailer store list the trust
agent already fetched (google_immersive_product). Rule-based, no searches, no LLM. Information only: it never changes
a verdict or a score, and the narrator never sees it (its cached narrations stay valid).

Offered on WALK AWAY (the listing is risky; here's a safe place to buy) and on WAIT when the store is cheaper than the
listing. Nothing qualifies -> None: Clarity never guesses a store.

Run: .venv/bin/python alternatives.py   (self-check, 0 searches, no database)
"""
import re

from locales import money

Z_BAND = 2.0  # = trust.Z_TOO_CHEAP / Z_TOO_EXPENSIVE: a store price outside the band isn't a fair price either


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _is_own(store, listing, store_domain=None):
    """The store the listing is already on: Amazon for an Amazon listing, the advertised shop for a screenshot."""
    name = _norm(store)
    own = [_norm(listing.get("marketplace"))]
    if store_domain:
        own.append(_norm(store_domain.split(".")[0]))
    return any(o and (o in name or name in o) for o in own if o not in ("", "screenshot"))


def best(listing, market, store_domain=None):
    """The cheapest store that is a major retailer itself (never a marketplace seller on its platform, never an unknown
    shop), has a link, prices inside the market band (median ± 2 sigma) and isn't the listing's own store. None if no
    store qualifies or there is no band (secondhand and parts listings have no store list)."""
    median, sigma = market.get("median"), market.get("sigma")
    if not (median and sigma):
        return None
    ok = [s for s in market.get("stores") or []
          if s.get("major") and s.get("link") and s.get("price")
          and abs(s["price"] - median) <= Z_BAND * sigma and not _is_own(s["store"], listing, store_domain)]
    if not ok:
        return None
    s = min(ok, key=lambda s: s["price"])  # ties: the first in the store list
    return {"store": s["store"], "price": s["price"], "link": s["link"], "median": median,
            "observed_at": market.get("stores_observed_at")}


def offer(final_verdict, listing, market, store_domain=None):
    """What the verdict shows under "Buy it here instead", or None. walk_away: the best trusted store. wait: the best
    trusted store, only if it's cheaper than this listing. buy_now: None (the plan's scope; buying here is fine)."""
    if final_verdict not in ("walk_away", "wait"):
        return None
    alt = best(listing, market, store_domain)
    if not alt:
        return None
    price = listing.get("price")
    if final_verdict == "wait" and (price is None or alt["price"] >= price):
        return None
    saving = round(price - alt["price"], 2) if price is not None and alt["price"] < price else None
    mkt = listing.get("market")
    if saving is not None:
        text = f"{alt['store']} sells this product for {money(alt['price'], mkt)}, {money(saving, mkt)} less than this listing."
    else:
        text = (f"{alt['store']} sells this product for {money(alt['price'], mkt)}, within the usual new-retail range "
                f"(median {money(alt['median'], mkt)}).")
    return {**alt, "saving": saving, "text": text, "price_text": money(alt["price"], mkt),
            "saving_text": saving is not None and money(saving, mkt) or None}


if __name__ == "__main__":
    band = lambda *stores: {"median": 300.0, "sigma": 20.0, "stores_observed_at": "2026-10-08T00:00:00Z",
                            "stores": [{"store": n, "price": p, "link": f"https://{_norm(n)}.example/p", "major": m}
                                       for n, p, m in stores]}
    amazon = {"marketplace": "amazon", "price": 320.0}
    ebay = {"marketplace": "ebay", "price": 59.0}
    shot = {"marketplace": "screenshot", "price": 59.0}

    # Walk away: the cheapest major retailer inside the band.
    a = offer("walk_away", ebay, band(("Best Buy", 289.0, True), ("Target", 279.0, True), ("Walmart", 299.0, True)))
    assert (a["store"], a["price"]) == ("Target", 279.0) and "$279.00" in a["text"] and a["saving"] is None, a
    # Never an unknown shop or a marketplace seller on a big platform, however cheap.
    a = offer("walk_away", ebay, band(("DealzShop", 265.0, False), ("Walmart - SomeSeller", 270.0, False),
                                      ("Best Buy", 289.0, True)))
    assert a["store"] == "Best Buy", a
    # A store price outside the band is not a fair price: below median - 2 sigma (too cheap) or above + 2 sigma.
    a = offer("walk_away", ebay, band(("Target", 250.0, True), ("Walmart", 345.0, True), ("Best Buy", 299.0, True)))
    assert a["store"] == "Best Buy", a
    # Edges of the band count (|z| == 2).
    assert offer("walk_away", ebay, band(("Target", 260.0, True)))["store"] == "Target"
    # The listing's own store is never the alternative.
    a = offer("wait", amazon, band(("Amazon.com", 290.0, True), ("Best Buy", 295.0, True)))
    assert a["store"] == "Best Buy" and a["saving"] == 25.0 and "$25.00 less" in a["text"], a
    assert offer("walk_away", shot, band(("Best Buy", 289.0, True)), store_domain="bestbuy.com") is None
    # Wait: only when it's cheaper than the listing.
    assert offer("wait", {"marketplace": "amazon", "price": 280.0}, band(("Best Buy", 289.0, True))) is None
    assert offer("wait", {"marketplace": "amazon", "price": None}, band(("Best Buy", 289.0, True))) is None
    # Buy now: never.
    assert offer("buy_now", amazon, band(("Best Buy", 289.0, True))) is None
    # No band, no stores, no link -> None. Missing data is never filled in.
    assert offer("walk_away", ebay, {**band(("Best Buy", 289.0, True)), "median": None}) is None
    assert offer("walk_away", ebay, band()) is None
    nolink = band(("Best Buy", 289.0, True))
    nolink["stores"][0]["link"] = None
    assert offer("walk_away", ebay, nolink) is None
    # A walk-away with a price shows the saving too (the scam-cheap listing is cheaper: no saving, band line instead).
    a = offer("walk_away", {"marketplace": "ebay", "price": 399.0}, band(("Best Buy", 289.0, True)))
    assert a["saving"] == 110.0 and "$110.00 less" in a["text"], a
    print("ALTERNATIVES SELF-CHECK PASSED (0 searches)")
