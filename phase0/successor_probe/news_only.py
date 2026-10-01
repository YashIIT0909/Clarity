"""Prompt 4, step 1: Google News lifecycle query only (1 search per product), classified with lifecycle.py.
News is the only source here, so find_successor_candidates can't corroborate (it needs 2): the next identifier is
classified directly with classify_release_status. Files in out/ are reused (re-runs cost 0).
Run: ../../backend/.venv/bin/python news_only.py   (from this dir)"""
from datetime import date

from probe import ENGINES, OUT, call  # noqa: E402  (probe.py sets up the path, env and US locale)
from lifecycle import classify_release_status, news_rows, parse_model  # noqa: E402

PRODUCTS = {"motorola-ma1": ("Motorola MA1", "Motorola"), "galaxy-tab-s11": ("Samsung Galaxy Tab S11", "Samsung")}
# Follow-up (option 3): the successor's own name, not "... successor", which found only 8 MA1 headlines.
# Recent only. SerpApi rejects so=1 (date sort) with q ("`q` and `so` parameters can't be used together"), so this uses
# Google News's own when: operator inside the query: headlines from the last 3 months.
QUERIES = {"motorola-ma2": ("Motorola MA1", "Motorola", {"q": "Motorola MA2"}),
           "motorola-ma2-recent": ("Motorola MA1", "Motorola", {"q": "Motorola MA2 when:3m"})}

for key, (name, brand, *q) in {**PRODUCTS, **QUERIES}.items():
    params = {**ENGINES["google_news"](key, name), **(q[0] if q else {})}
    news = news_rows(call(params, OUT / f"{key}_google_news.json"))
    family, ident, sep = parse_model(name, brand)
    print(f"\n=== {name}: family {family!r} + {ident}; {len(news)} headlines")
    for n in range(ident + 1, ident + 3):
        s = classify_release_status({"name": f"{brand} {family}{sep}{n}", "family": family, "identifier": n},
                                    news, date.today())
        print(f"  {s['candidate']}: {s['status'].upper()}  since {s['since']}  window {s['release_window']}  "
              f"date {s['release_date']}  passed {s['date_passed']}")
        for e in s["evidence"]:
            print(f"     {e['date'][:10]}  {e['stage']:9} {(e['source'] or '')[:18]:18} {e['title'][:95]}")
    # Headlines naming the family at all, classified or not: shows what the keyword rules skipped.
    print("  all headlines mentioning the family:")
    for h in sorted(news, key=lambda h: h["date"] or "", reverse=True):
        if family.lower().replace(" ", "") in h["title"].lower().replace(" ", ""):
            print(f"     {(h['date'] or '?')[:10]}  {(h['source'] or '')[:18]:18} {h['title'][:100]}")
