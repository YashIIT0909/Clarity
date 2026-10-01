"""Summarise out/*.json from probe.py. Costs 0 searches.
Run: ../../backend/.venv/bin/python analyze.py   (from this dir)"""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).parent
OUT = HERE / "out"
sys.path.insert(0, str(HERE.parent.parent / "backend"))
os.chdir(HERE.parent.parent / "backend")
from demo_products import DEMO_PRODUCTS  # noqa: E402
from trust import MAJOR_RETAILERS  # noqa: E402

RETAIL_KEYS = {re.sub(r"[^a-z]", "", n.removesuffix(".com")) for n in MAJOR_RETAILERS}

# engine -> (result list key, field holding the page URL)
ROWS = {
    "lens_exact_matches": ("exact_matches", "link"),
    "lens_visual_matches": ("visual_matches", "link"),
    "lens_products": ("visual_matches", "link"),
    "google_reverse_image": ("image_results", "link"),
    "bing_reverse_image": ("pages_with_this_image", "source"),  # `link` is a bing.com detail page
    "yandex_reverse_image": ("image_results", "link"),
}
REVERSE = ["google_reverse_image", "bing_reverse_image", "yandex_reverse_image"]


def domain(url):
    """Registered domain without tldextract: amazon.co.uk -> amazon.co.uk, www.bestbuy.com -> bestbuy.com."""
    parts = (urlparse(url or "").hostname or "").removeprefix("www.").split(".")
    n = 3 if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "org", "net", "ne", "or"} else 2
    return ".".join(parts[-n:])


def is_retailer(d):
    """Same list trust._market uses (store names), matched on the domain's name label: bhphotovideo ~ "b&h photo-video-audio"."""
    label = d.split(".")[0]
    return label in RETAIL_KEYS or (len(label) >= 5 and any(k.startswith(label) for k in RETAIL_KEYS))


def rows(product, engine, suffix=""):
    d = json.loads((OUT / f"{product}_{engine}{suffix}.json").read_text())
    key, field = ROWS[engine]
    return d, [r for r in d.get(key, []) if isinstance(r, dict) and r.get(field)], field


def summarise(product, engine, suffix=""):
    d, rs, field = rows(product, engine, suffix)
    match = DEMO_PRODUCTS[product]["match"]
    doms = {domain(r[field]) for r in rs}
    return {
        "n": len(rs), "domains": doms, "retail": sorted(x for x in doms if is_retailer(x)),
        "dated": sum(bool(r.get("date")) for r in rs),
        "priced": sum(bool(r.get("price") or r.get("extracted_price")) for r in rs),
        # sanity: does the row's title even name the product?
        "on_topic": sum(all(re.search(m, r.get("title") or "", re.I) for m in match) for r in rs),
        "topic_domains": {domain(r[field]) for r in rs if all(re.search(m, r.get("title") or "", re.I) for m in match)},
        "lat": d.get("_probe_latency_s"), "err": d.get("error"),
    }


def main():
    for p in DEMO_PRODUCTS:
        print(f"\n### {p}")
        print(f"| engine | rows | on-topic titles | domains | retailer domains | dated | priced | latency s |")
        print("|---|---|---|---|---|---|---|---|")
        s = {e: summarise(p, e) for e in ROWS}
        for e, x in s.items():
            print(f"| {e} | {x['n']} | {x['on_topic']} | {len(x['domains'])} | {len(x['retail'])} | "
                  f"{x['dated']} | {x['priced']} | {x['lat']} |" + (f" ERR {x['err']}" if x["err"] else ""))
        lens = s["lens_exact_matches"]["domains"]
        print(f"lens exact retailer domains: {', '.join(s['lens_exact_matches']['retail'])}")
        print(f"lens exact top domains: {', '.join(sorted(lens)[:40])}{' …' if len(lens) > 40 else ''}")
        for e in REVERSE:
            new = s[e]["domains"] - lens
            new_topic = s[e]["topic_domains"] - lens
            print(f"{e}: {len(new)} domains not in lens exact ({len([x for x in new if is_retailer(x)])} retailers);"
                  f" {len(new_topic)} of them from rows whose title names the product: {', '.join(sorted(new_topic)[:30])}")

    # upload vs URL
    a, b = summarise("sony-xm5", "lens_exact_matches"), summarise("sony-xm5", "lens_exact_matches", "_upload")
    both = a["domains"] & b["domains"]
    print(f"\n### upload vs url (sony-xm5 lens exact): url {a['n']} rows/{len(a['domains'])} domains, "
          f"upload {b['n']} rows/{len(b['domains'])} domains, shared {len(both)} "
          f"(jaccard {len(both) / len(a['domains'] | b['domains']):.2f}); "
          f"retailers url {a['retail']} upload {b['retail']}; latency url {a['lat']}s upload {b['lat']}s")


if __name__ == "__main__":
    assert domain("https://www.amazon.co.uk/x") == "amazon.co.uk" and domain("https://shop.bestbuy.com/a") == "bestbuy.com"
    assert is_retailer("bhphotovideo.com") and is_retailer("amazon.ca") and not is_retailer("reddit.com") and not is_retailer("mac.bid")
    main()
