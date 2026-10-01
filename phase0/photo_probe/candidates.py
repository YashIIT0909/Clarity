"""Prompt 2: walk-away candidates from cached eBay searches (0 searches to find them).
Lens exact first on every candidate; Bing only on finalists. Files in out/<item_id>_<engine>.json are reused.
Run: ../../backend/.venv/bin/python candidates.py [bing ITEM_ID ...]"""
import json
import re
import sys
from collections import defaultdict

from probe import ENGINES, OUT, call
from analyze import domain, is_retailer

# item_id -> (pattern, seller, feedback, condition, price) — from the cached eBay search rows, picked by eye.
CANDIDATES = {
    "168680000095": (1, "saint--4203", 0, "Brand New", 60.0),
    "178520199264": (1, "hern292453", 0, "Brand New", 80.0),
    "318906537658": (1, "ty_854112", 0, "Brand New", 65.0),
    "407220109862": (1, "al_83093749", 0, "Brand New", 100.0),
    "800697221579": (1, "vpye3094", 0, "Brand New", 65.0),
    "820095821635": (1, "zotte.42", 0, "Brand New", 99.99),
    "158331596684": (1, "sellerseli", 0, "Pre-Owned", 44.99),
    "407219333647": (2, "reliablegoods-99", 4, "Pre-Owned", 79.99),
    "287595183543": (2, "cr072572", 1, "Open Box", 60.0),
    "307193302287": (2, "ebe_3765", 0, "Brand New", 59.99),
}
MANUFACTURER = {"apple.com"}
ITEM = re.compile(r"ebay\.[a-z.]+/itm/(?:[^/?]+/)?(\d{9,})")


def image_url(item_id):
    return json.loads((OUT / "cand_images.json").read_text())[item_id]


def matches(item_id, engine):
    key, field = {"lens_exact_matches": ("exact_matches", "link"),
                  "bing_reverse_image": ("pages_with_this_image", "source")}[engine]
    p = OUT / f"{item_id}_{engine}.json"
    if not p.exists():
        return None
    return [(r[field], r.get("title"), r.get("price"), r.get("date")) for r in json.loads(p.read_text()).get(key, [])
            if r.get(field)]


def classify(item_id, rows):
    """Split matches: manufacturer/retailer pages vs OTHER marketplace listings (deduped by item ID) vs the rest."""
    out = defaultdict(list)
    seen_items = set()
    for url, title, price, date in rows:
        d = domain(url)
        m = ITEM.search(url)
        if m:
            if m.group(1) == item_id or m.group(1) in seen_items:
                continue  # same listing, or already counted
            seen_items.add(m.group(1))
            out["other_marketplace_listing"].append((m.group(1), title, price))
        elif "ebay." in d:
            out["ebay_category_or_search_page"].append(url)
        elif d in MANUFACTURER:
            out["manufacturer"].append(url)
        elif is_retailer(d):
            out["retailer"].append(url)
        elif re.search(r"mercari|poshmark|offerup|facebook|craigslist|kijiji|vinted|depop|wallapop|olx|avito", d):
            out["other_marketplace_listing"].append((url, title, price))
        else:
            out["other"].append(url)
    return out


if __name__ == "__main__":
    engines = ["lens_exact_matches"]
    ids = list(CANDIDATES)
    if sys.argv[1:2] == ["bing"]:
        engines, ids = ["bing_reverse_image"], sys.argv[2:]
    for i in ids:
        for e in engines:
            d = call(ENGINES[e](image_url(i)), OUT / f"{i}_{e}.json")
            print(i, e, d.get("_probe_latency_s"), d.get("error", ""))
    for i, c in CANDIDATES.items():
        print(f"\n## {i} pattern {c[0]} {c[1]} fb={c[2]} {c[3]} ${c[4]}")
        for e in ["lens_exact_matches", "bing_reverse_image"]:
            rows = matches(i, e)
            if rows is None:
                continue
            cl = classify(i, rows)
            print(f"  {e}: {len(rows)} rows -> " + ", ".join(f"{k}={len(v)}" for k, v in cl.items()))
            for k in ["manufacturer", "retailer", "other_marketplace_listing"]:
                for v in cl.get(k, [])[:8]:
                    print(f"    {k}: {v}")
