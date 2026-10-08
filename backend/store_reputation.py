"""Store reputation: what does the web know about a store domain that isn't a known retailer or marketplace?
Evidence + rule-based signals, no score. Five searches per domain, all through serpapi_cache.
Wording rule: reports *mention* complaints; nothing here ever calls a store a scam.

Calibrated on real data (2026-09-29): amazon.com, bestbuy.com, walmart.com, woestejongenstrouwfilms.nl (an SEO spam
site whose every Google result is on its own domain), portatilshoprd.com (a small Dominican computer shop) and
aseertime.com (spam product pages on a domain named like a dessert chain).
"""
import asyncio
import re
from datetime import datetime, timezone

import locales
import serpapi
from domains import registered_domain

TIMEOUT = 25  # s per source, inside trust's 30s budget. Google Forums took over 20s for 3 of 6 domains on
              # 2026-09-29; a slow source is unavailable, the rest still count
# "Report Fraud" is a footer link on review sites (Zomato), not a complaint.
COMPLAINT = re.compile(r"scam|fake|(?<!report )fraud|didn'?t receive|did not receive|never (?:arrived|received)", re.I)
# Fewer independent "<domain> reviews" results than this (and no entity info) = no web footprint. Independent = not on
# the domain itself. Real counts: woestejongenstrouwfilms.nl 0 (all 9 results are its own pages), amazon.com 2,
# walmart.com 4, portatilshoprd.com 5, bestbuy.com 6, aseertime.com 7 (a namesake restaurant). Counts don't separate
# big from unknown sites (Google shows ~10 per page), so N only catches the empty case; About This Result carries the rest.
MIN_INDEPENDENT = 3
MIN_COMPLAINTS = 2


def queries(domain, market=None):
    """name -> (engine, params), in the shop's market (US: gl=us, ads region 2840; India: gl=in, region 2356).
    Param names from SerpApi's docs (2026-09-29)."""
    loc = locales.get(market)
    return {
        "reviews": ("google", {"q": f"{domain} reviews", "gl": loc["gl"], "hl": "en"}),
        "scam": ("google", {"q": f"{domain} scam", "gl": loc["gl"], "hl": "en"}),
        "forums": ("google_forums", {"q": domain, "gl": loc["gl"], "hl": "en"}),
        # docs: q is the site URL itself ("https://www.example.com/")
        "about": ("google_about_this_result", {"q": f"https://{domain}/"}),
        "ads": ("google_ads_transparency_center", {"text": domain, "region": loc["ads_region"]}),
    }


async def _call(engine, params):
    try:
        return await asyncio.wait_for(asyncio.to_thread(serpapi.search, engine, **params), TIMEOUT), None
    except Exception as e:  # one source failing must not fail the check
        return None, f"{type(e).__name__}: {e}"[:200] if str(e) else type(e).__name__


def _about(data):
    s = ((data or {}).get("about_this_result") or {}).get("about_the_source") or {}
    desc = s.get("description") or {}
    return {
        "title": s.get("title"), "entity_type": s.get("subtitle"),  # e.g. "Computer store in Santo Domingo"
        "description": desc.get("snippet"), "wikipedia": "wikipedia.org" in (desc.get("link") or ""),
        "known_entity": bool(s.get("subtitle") or "wikipedia.org" in (desc.get("link") or "")),
    }


def _ads(data):
    """CONTEXT ONLY: never scored. Absent proves nothing; a dubious shop can buy ads too."""
    c = (data or {}).get("ad_creatives") or []
    first = min((a["first_shown"] for a in c if a.get("first_shown")), default=None)
    return {"runs_ads": bool(c), "advertisers": sorted({a["advertiser"] for a in c if a.get("advertiser")})[:5],
            "creatives_seen": len(c),
            "earliest_first_shown": first and datetime.fromtimestamp(first, timezone.utc).date().isoformat()}


async def run(domain, market=None):
    q = queries(domain, market)
    got = dict(zip(q, await asyncio.gather(*(_call(*q[name]) for name in q))))
    data = {name: d for name, (d, _) in got.items()}
    unavailable = [{"source": name, "reason": err} for name, (_, err) in got.items() if err]

    independent = lambda rows: [r for r in rows if r.get("link") and registered_domain(r["link"]) != domain]
    reviews = independent((data["reviews"] or {}).get("organic_results") or [])
    pool = [r for name in ("reviews", "scam") for key in ("organic_results", "discussions_and_forums")
            for r in (data[name] or {}).get(key) or []] + ((data["forums"] or {}).get("organic_results") or [])
    complaints = {}
    for r in independent(pool):
        text = f"{r.get('title') or ''} | {r.get('snippet') or ''}"
        if (m := COMPLAINT.search(text)) and r["link"] not in complaints:  # one hit per thread/page
            complaints[r["link"]] = {"source": r.get("source") or registered_domain(r["link"]), "url": r["link"],
                                     "title": r.get("title"), "snippet": r.get("snippet"), "matched": m.group(0)}
    hits = list(complaints.values())
    about = _about(data["about"])

    if about["wikipedia"]:
        footprint = "established"
    elif about["known_entity"] or len(reviews) >= MIN_INDEPENDENT:
        footprint = "thin"
    else:
        footprint = "none"

    signals = []
    n_sources = len({registered_domain(h["url"]) for h in hits})
    if data["about"] is None or data["reviews"] is None:
        signals.append({"signal": "no_web_footprint", "status": "unavailable",
                        "reason": "Couldn't check the site's web presence (not counted)", "evidence": []})
    elif not about["known_entity"] and len(reviews) < MIN_INDEPENDENT:
        signals.append({"signal": "no_web_footprint", "status": "fired",
                        "reason": f"Google has no description of {domain} ({about['description'] or 'no source info'})"
                                  + (f", and only {len(reviews)} page(s) off the site mention it with reviews" if reviews
                                     else ", and no page off the site itself mentions it with reviews"),
                        "evidence": [{"about_this_result": about["description"]}] + reviews})
    else:
        signals.append({"signal": "no_web_footprint", "status": "not_fired",
                        "reason": f"The site has a web presence ({about['entity_type'] or about['title']}; "
                                  f"{len(reviews)} independent review results)", "evidence": []})
    if footprint == "established":
        # Big brands attract impersonation-scam news (phishing, fake-support calls) that a keyword can't tell apart
        # from complaints about the store itself, so the count isn't used for them. The hits are still reported.
        signals.append({"signal": "store_complaints", "status": "not_fired",
                        "reason": (f"Established site: {len(hits)} page(s) mention scams or fraud near its name, "
                                   f"expected at this scale (mostly impersonation scams); not counted") if hits
                                  else "Established site, and no complaint mentions found",
                        "evidence": []})
    elif len(hits) >= MIN_COMPLAINTS:
        signals.append({"signal": "store_complaints", "status": "fired",
                        "reason": f"Reports on {n_sources} independent site(s) mention problems with {domain} "
                                  f"({', '.join(sorted({h['matched'].lower() for h in hits}))})", "evidence": hits})
    else:
        signals.append({"signal": "store_complaints", "status": "not_fired",
                        "reason": f"{len(hits)} independent page(s) mention complaints (fewer than {MIN_COMPLAINTS})"
                                  + ("; the forum search was unavailable" if data["forums"] is None else ""),
                        "evidence": hits})
    return {"domain": domain, "footprint": footprint, "independent_review_results": len(reviews),
            "complaint_hits": hits, "about_this_result": about, "ads": _ads(data["ads"]),
            "sources_unavailable": unavailable, "signals": signals}


if __name__ == "__main__":
    import sys

    # Cached data only: every domain below was fetched once on 2026-09-29.
    serpapi.httpx.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("network blocked: cache only"))
    want = {"amazon.com": ("established", set()), "bestbuy.com": ("established", set()),
            "walmart.com": ("established", set())}
    domains = sys.argv[1:] or ["amazon.com", "bestbuy.com", "walmart.com", "woestejongenstrouwfilms.nl",
                               "portatilshoprd.com", "aseertime.com"]
    failures = []
    for d in domains:
        r = asyncio.run(run(d))
        fired = {s["signal"] for s in r["signals"] if s["status"] == "fired"}
        a, ads = r["about_this_result"], r["ads"]
        print(f"\n=== {d}: footprint {r['footprint'].upper()}  fired: {sorted(fired) or 'none'}")
        print(f"  about this result: {a['title']!r} | type {a['entity_type']!r} | wikipedia {a['wikipedia']} | {a['description']!r}"[:230])
        print(f"  independent 'reviews' results: {r['independent_review_results']} | complaint hits: {len(r['complaint_hits'])}")
        for h in r["complaint_hits"][:8]:
            print(f"     [{h['matched']}] {h['source'][:28]:28} {h['title'][:80]}")
        print(f"  ads (context only): {ads}")
        if r["sources_unavailable"]:
            print(f"  unavailable: {r['sources_unavailable']}")
        for s in r["signals"]:
            print(f"  [{s['status']:9}] {s['signal']}: {s['reason']}")
        if d in want and (r["footprint"], fired) != want[d]:
            failures.append(d)
        assert not re.search(r"is a scam|scammer", str(r["signals"]), re.I), "wording: reports mention, never accuse"

    # Synthetic: complaints on 2 independent sites about an unknown shop fire store_complaints; on the shop's own
    # pages they don't count.
    real = serpapi.search
    fake = {"google": {"organic_results": [
        {"link": "https://www.reddit.com/r/Scams/1", "title": "shop-x.com never arrived", "snippet": ""},
        {"link": "https://www.trustpilot.com/review/shop-x.com", "title": "shop-x.com reviews", "snippet": "total scam"},
        {"link": "https://shop-x.com/blog/scam", "title": "Is it a scam? No!", "snippet": ""}]}}
    serpapi.search = lambda engine, **p: fake.get(engine, {})
    r = asyncio.run(run("shop-x.com"))
    serpapi.search = real
    got = {s["signal"]: s for s in r["signals"]}
    ok = got["store_complaints"]["status"] == "fired" and len(r["complaint_hits"]) == 2 \
        and got["no_web_footprint"]["status"] == "fired"
    print(f"\nsynthetic: 2 independent complaints fire store_complaints, self-hosted ignored: {'ok' if ok else 'FAILED'}")
    failures += [] if ok else ["synthetic"]
    print("EXPECTATION FAILURES:", failures or "none")
    sys.exit(1 if failures else 0)
