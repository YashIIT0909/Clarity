"""Successor Radar: is a newer model of this product named in searches or news, and is it rumored, announced or out?
Rule-based, no SerpApi calls here: callers pass Trends rising related queries, Google Autocomplete suggestions and
Google News results.

1. find_successor_candidates: a candidate names the same family with a higher identifier (XM5 -> XM6, Pro 2 -> Pro 3)
   or a generation word, corroborated by 2+ independent sources. Competitors ("airpod max 2" for AirPods Pro 2) don't
   name the family; older models, accessories and revisions of the same model ("Updated Switch 2") are never
   candidates. status: "found", "none" (usable data, no candidate), "unavailable" (no model identifier, or fewer than
   2 usable sources). Neither "none" nor "unavailable" is a signal or a penalty.
2. classify_release_status: released / announced / rumored / none for one candidate, from dated headlines that name
   its family + identifier. Headlines only; article bodies are never read or stored.

Run: .venv/bin/python lifecycle.py   (self-check on phase0/successor_probe/out, 0 searches)
"""
import calendar
import re
from datetime import date, datetime

SOURCES = ("trends", "autocomplete", "news")
MAX_ID = 100  # generation numbers are small (XM5, S25, iPhone 17); 360, 612, 1000 are model codes or specs
MAX_JUMP = 3  # a successor is at most 3 generations ahead
ACCESSORY = re.compile(r"\b(case|cover|tips|charger|ear ?pads?|cushions?|strap|stand|dock|screen protector|skin|cable"
                       r"|controller|grip|replacement|repair|parts?)\b", re.I)
NEXT_GEN = re.compile(r"\bnext[\s-]?gen(eration)?\b", re.I)
# Numbers that are units or quantities, never a model generation.
UNITS = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:gb|tb|mb|mah|inch(?:es)?|mm|cm|watts?|volts?|hz|dpi|mp|fps|oz|lbs?|ft|ch|pack|count|pcs"
    r"|pieces|ct)\b"
    r"|\b\d+(?:\.\d+)?(?:w|v|k|p|g|in)\b"               # attached single-letter units: 1500W, 4K, 1080p, 5G
    r"|\b\d+(?:\.\d+)?\s?[\"”]"                          # 65"
    r"|\b\d+-(?:inch|pack|piece|count)\b|\b(?:pack|set) of \d+\b"
    r"|\b(?:19|20)\d\d\b", re.I)                          # years ("2026 deals", "(2025 Model)")


def parse_model(title, brand=None):
    """Model family + generation identifier from a product name or a long listing title, or None if unsure.
    'Sony WH-1000XM5 Premium Noise Cancelling…' -> ('WH-1000XM', 5, ''); 'Apple AirPods Pro 2nd Gen Earbuds w/…' ->
    ('AirPods Pro', 2, ' '). Marketing after the first comma/dash/paren is cut, units and quantities are removed, and
    exactly one small number must remain, standalone or attached to the model token (XM5, S25). Model codes (HCE200W)
    and two different numbers are ambiguous: None. Never guesses."""
    s = re.split(r"\s[-–|(]|[,(]", title)[0]
    s = re.sub(r"\b(\d+)(?:st|nd|rd|th)\s+gen(?:eration)?\b", r"\1", s, flags=re.I)  # "2nd Gen" -> 2
    s = re.sub(r"\bgen(?:eration)?\s*(\d+)\b", r"\1", s, flags=re.I)                 # "Gen 3" -> 3
    s = UNITS.sub(" ", s)
    if brand and s.lower().startswith(brand.lower() + " "):
        s = s[len(brand) + 1:]
    tokens = s.split()
    hits = [(i, m) for i, t in enumerate(tokens)
            if (m := re.fullmatch(r"(.*?[a-z-])?(\d+)", t, re.I)) and int(m.group(2)) < MAX_ID]
    if len({int(m.group(2)) for _, m in hits}) != 1:
        return None
    i, m = hits[0]
    family = " ".join(tokens[:i] + [m.group(1) or ""]).strip()
    return (family, int(m.group(2)), "" if m.group(1) else " ") if family else None


def search_term(title, brand):
    """Clean search term for Trends/Shopping from a listing title: brand's first word + family + identifier
    ('Motorola Sound MA1, the Only Google…' -> 'Motorola MA1'). None when parse_model can't tell (caller falls back)."""
    p = brand and parse_model(title, brand)
    if not p:
        return None
    b = brand.split()[0]
    return f"{b} {re.sub(rf'^{re.escape(b)}\s+', '', p[0], flags=re.I)}{p[2]}{p[1]}"


def _body(family):
    # spaces/hyphens inside the family are optional: "wh-1000xm" matches "wh1000xm" and "wh 1000 xm"
    return r"\b" + r"[\s-]?".join(re.escape(t) for t in re.split(r"[\s-]+", family))


# ponytail: a query counts as this brand's if it names the family; a rival reusing a one-word family ("Switch") would
# slip through. Add a competitor-brand list if that shows up in real data.
def _family_re(family):
    return re.compile(rf"{_body(family)}(?:[\s-]?(\d+)\b|\s+(?:gen(?:eration)?\s*(\d+)|(\d+)(?:st|nd|rd|th)\s+gen))", re.I)


def _ids(family, text):
    return {int(next(g for g in m.groups() if g)) for m in _family_re(family).finditer(text)}


def names_product(term, product_name, brand=None):
    """Does a Trends term name this product's family? 'space heater' for a Honeywell 360 doesn't: a category's
    rising queries say nothing about this product's lifecycle."""
    p = parse_model(product_name, brand)
    return bool(p and re.search(_body(p[0]), term, re.I))


def news_rows(data):
    """Flatten google_news news_results (incl. `highlight` and `stories`) into {title, source, date, link}."""
    out = []
    for r in (data or {}).get("news_results", []):
        for s in ([r] if "title" in r else []) + ([r["highlight"]] if "highlight" in r else []) + r.get("stories", []):
            out.append({"title": s.get("title", ""), "source": (s.get("source") or {}).get("name"),
                        "date": s.get("iso_date"), "link": s.get("link")})
    return out


def find_successor_candidates(product_name, trends_rising, autocomplete, news=None, brand=None):
    """product_name: a clean name or the listing title (parse_model cuts the marketing). trends_rising:
    related_queries.rising rows, or None when the Trends term doesn't name the product (see names_product).
    autocomplete: suggestion strings. news: news_rows(); None if the engine didn't run."""
    parsed = parse_model(product_name, brand)
    inputs = {"trends": trends_rising, "autocomplete": autocomplete, "news": news}
    usable = [s for s in SOURCES if inputs[s]]
    result = {"status": "unavailable", "candidates": [], "context": [], "rejected": [], "sources_used": usable}
    if not parsed or len(usable) < 2:
        return result
    family, ident, sep = parsed
    model = rf"{_body(family)}[\s-]?{ident}\b"
    # A revision of this very model: the revision word must attach to the model name, not just share a headline
    # ("Updated Switch 2", "Switch 2 model with...", but not "pre-order details have been updated").
    revision = re.compile(rf"\b(updated|revised|refreshed)\s+(\w+\s+)?{model}"
                          rf"|{model}\s+(model|with (a |an )?(new|updated)|oled|lite|slim|revision)\b", re.I)

    rows = [("trends", r["query"], r.get("value")) for r in trends_rising or []] \
        + [("autocomplete", s, None) for s in autocomplete or []] \
        + [("news", n["title"], n) for n in news or []]
    found = {}  # identifier -> [evidence]
    for source, text, extra in rows:
        ids = _ids(family, text)
        gen_word = NEXT_GEN.search(text) and re.search(_body(family), text, re.I)
        if not ids and not gen_word:
            continue  # doesn't name the family: competitor, category or game ("spiritual successor to Wii Sports")
        newer = {n for n in ids if ident < n <= ident + MAX_JUMP} | ({"next-gen"} if gen_word else set())
        if source != "news" and ACCESSORY.search(text):
            result["rejected"].append({"source": source, "text": text, "why": "accessory"})
            continue
        if not newer:  # the product itself isn't recorded: it's most rows and says nothing
            if revision.search(text):
                result["context"].append({"source": source, "text": text, "why": "revision of the same model"})
            elif ids and max(ids) < ident:
                result["rejected"].append({"source": source, "text": text, "why": "older model"})
            continue
        ev = {"source": source, "text": text, "vs": bool(re.search(r"\bvs\.?\b", text, re.I))}
        if source == "trends":
            ev["value"] = extra
        if source == "news":
            ev.update(source_name=extra["source"], date=extra["date"], link=extra["link"])
        for n in newer:
            found.setdefault(n, []).append(ev)

    prefix = f"{brand} " if brand else ""
    for n, evidence in sorted(found.items(), key=lambda kv: str(kv[0])):
        sources = sorted({e["source"] for e in evidence})
        if len(sources) < 2:
            result["rejected"].append({"source": sources[0], "text": evidence[0]["text"],
                                       "why": f"uncorroborated ({len(evidence)} mention(s), 1 source)"})
            continue
        trends = [e["value"] for e in evidence if e["source"] == "trends"]
        result["candidates"].append({
            "name": f"next-gen {prefix}{family}" if n == "next-gen" else f"{prefix}{family}{sep}{n}",
            "family": family, "identifier": n, "sources": sources, "evidence": evidence,
            "trends_value": "Breakout" if "Breakout" in trends else max(
                trends, key=lambda v: int(re.sub(r"\D", "", v) or 0), default=None)})
    result["status"] = "found" if result["candidates"] else "none"
    return result


# --- Release status -----------------------------------------------------------------------------------------------

STAGES = ("rumored", "announced", "released")  # ordered: a product only moves forward
STAGE_WORDS = {  # checked most advanced first; rumor words veto "announced" ("Leak: Tab S12 revealed", "release date
                 # expectations" are rumors, not announcements)
    "released": r"\b(reviews?|reviewed|tested|now available|available now|on sale now|out now|is out|launch(es|ed)"
                r"|goes on sale|in stock|ships|shipping now|deals?|discount(ed)?|lowest price|price drop)\b",
    "announced": r"\b(announce[sd]?|unveil(s|ed)?|officially reveals?|goes official|revealed|pre-?orders?|release date|launch date"
                 r"|coming (on|in)|arrives|due)\b",
    "rumored": r"\b(rumou?rs?|rumou?red|leaks?|leaked|reportedly|report|could|might|may|tipped|spotted|fcc|expect(s|ed|ations)?"
               r"|coming soon|hints?|teases?|what we know)\b",
}
STALE_DAYS = 365
MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
WINDOW = re.compile(rf"\b{MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(20\d\d))?"   # Oct 15 (, 2026)
                    rf"|\b(\d{{1,2}})\s+{MONTH}(?:\s+(20\d\d))?"                     # 5 June (2026)
                    rf"|\b{MONTH}\s+(20\d\d)\b"                                       # November 2026
                    rf"|\b(q[1-4]|early|late|spring|summer|fall|autumn|winter|holiday)\s+(20\d\d)\b", re.I)


def _day(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).date()


def _window(title, published):
    """(text, date) for a stated release date/window. A month with no day -> the month's last day (so "passed" is
    only claimed once the whole month is over); a day with no year -> its next occurrence after publication."""
    m = WINDOW.search(title)
    if not m:
        return None, None
    g = m.groups()
    mon = next((x for x in (g[0], g[4], g[6]) if x), None)
    if not mon:  # quarter / season: stated, but no single date to compare
        return m.group(0), None
    month = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].index(mon[:3].lower()) + 1
    day, year = (int(g[1]), g[2]) if g[0] else (int(g[3]), g[5]) if g[4] else (None, g[7])
    y = int(year) if year else published.year
    d = date(y, month, day or calendar.monthrange(y, month)[1])
    if not year and d < published:
        d = d.replace(year=y + 1)
    return m.group(0), d


def classify_release_status(candidate, news, today):
    """candidate: from find_successor_candidates. news: news_rows(). today: date.
    Only dated headlines naming the candidate's family + identifier count, ordered by iso_date (never by result
    position). Status = the most advanced stage any of them shows: products move rumored -> announced -> released, so a
    later "announced" headline confirms a release but never undoes it. Every classified headline is kept, newest first,
    so conflicts stay visible."""
    n, family = candidate["identifier"], candidate["family"]
    evidence = []
    for h in news or []:
        names = NEXT_GEN.search(h["title"]) and re.search(_body(family), h["title"], re.I) if n == "next-gen" \
            else n in _ids(family, h["title"])
        if not (names and h.get("date")):
            continue
        hit = {s for s in STAGES if re.search(STAGE_WORDS[s], h["title"], re.I)}
        stage = "released" if "released" in hit else "rumored" if "rumored" in hit else "announced" if hit else None
        if stage:
            evidence.append({**h, "stage": stage, "day": _day(h["date"])})
    evidence.sort(key=lambda e: e["day"], reverse=True)
    out = {"candidate": candidate["name"], "status": "none", "weak": False, "since": None, "release_window": None,
           "release_date": None, "date_passed": False, "evidence": []}
    if not evidence:
        return out
    status = max((e["stage"] for e in evidence), key=STAGES.index)
    at_stage = [e for e in evidence if e["stage"] == status]
    out.update(status=status, since=at_stage[-1]["day"].isoformat(),  # the first dated headline at this stage
               evidence=[{k: e[k] for k in ("title", "source", "date", "link", "stage")} for e in evidence])
    if status == "rumored" and (today - at_stage[0]["day"]).days > STALE_DAYS:
        out.update(status="rumored (stale)", weak=True)
    if status == "announced":
        for e in at_stage:  # newest stated date wins
            text, d = _window(e["title"], e["day"])
            if text:
                out.update(release_window=text, release_date=d and d.isoformat(), date_passed=bool(d and d < today))
                break
    return out


# --- Runtime queries and scoring (used by timing.py) ---------------------------------------------------------------

TIMEOUT = 15  # s per engine; Trends took up to 7.8s in the probe


def queries(name, market=None):
    """The three runtime calls in the listing's market (engine, params): US geo=US / gl=us, India geo=IN / gl=in.
    Param names from SerpApi's docs (Prompt 1). Amazon Autocomplete was dropped: it named no successor that Google
    didn't."""
    import locales
    loc = locales.get(market)
    return {"trends": ("google_trends", {"q": name, "data_type": "RELATED_QUERIES", "date": "today 3-m", "geo": loc["geo"],
                                         "hl": "en"}),
            "autocomplete": ("google_autocomplete", {"q": name, "gl": loc["gl"], "hl": "en"}),
            "news": ("google_news", {"q": f"{name} successor", "gl": loc["gl"], "hl": "en"})}


RUMOR_RECENT_DAYS = 90
RUMOR_SCORE = -0.2  # x the lifecycle weight (0.25) = -0.05: flips only a timing score already within 0.05 of the line


def _cite(e):
    return f"“{e['title']}” ({e['source']}, {e['date'][:10]})"


def _rumor_corroborated(release, today):
    """Recent: newest rumor headline within 90 days. Corroborated: 3+ rumor headlines, 3+ outlets, 2+ calendar months."""
    r = [e for e in release["evidence"] if e["stage"] == "rumored"]
    recent = bool(r) and (today - _day(r[0]["date"])).days <= RUMOR_RECENT_DAYS
    return recent and len(r) >= 3 and len({e["source"] for e in r}) >= 3 and len({e["date"][:7] for e in r}) >= 2


def score(candidate, today):
    """(score in [-1, 0], reason or None) for one classified candidate. Negative = wait. Only an announced successor
    (strongest the nearer its date) or a recent, corroborated rumor pushes; everything else is context at 0. Never a
    price claim."""
    rel, name = candidate["release"], candidate["name"]
    first = lambda stage: next((e for e in reversed(rel["evidence"]) if e["stage"] == stage), None)  # oldest at stage
    newest = lambda stage: next((e for e in rel["evidence"] if e["stage"] == stage), None)
    if rel["status"] == "announced":
        e = first("announced")
        if rel["date_passed"]:
            return 0.0, (f"A newer model, {name}, was announced {_cite(e)} with a release date of {rel['release_date']}, "
                         f"which has passed; no headline confirms it's out yet")
        days = rel["release_date"] and (date.fromisoformat(rel["release_date"]) - today).days
        # ponytail: fixed proximity steps; tune once a real announced product is measured
        proximity = 0.5 if days is None else 1.0 if days <= 30 else 0.75 if days <= 90 else 0.5 if days <= 180 else 0.25
        when = f", due {rel['release_window']}" if rel["release_window"] else ", with no release date in the headlines"
        return -proximity, f"A newer model, {name}, has been announced {_cite(e)}{when}"
    if rel["status"] == "released":
        return 0.0, f"A newer model, {name}, is already out {_cite(first('released'))}"
    if rel["status"] == "rumored" and _rumor_corroborated(rel, today):
        n = sum(e["stage"] == "rumored" for e in rel["evidence"])
        return RUMOR_SCORE, (f"A newer model, {name}, is rumored: {n} leak/rumor headlines, newest {_cite(newest('rumored'))}"
                             f"; nothing official yet")
    if rel["status"].startswith("rumored"):
        return 0.0, f"A newer model, {name}, has only been rumored (newest {_cite(newest('rumored'))}); too little to go on"
    return 0.0, None


if __name__ == "__main__":
    import json
    from pathlib import Path

    from demo_products import DEMO_PRODUCTS

    # 1. Model identification on real-looking titles.
    def fam(title, brand=None):
        p = parse_model(title, brand)
        return p and (p[0].lower(), p[1])

    assert fam("Apple iPhone 17 Pro Max 256GB", "Apple") == ("iphone", 17)
    assert fam("Samsung Galaxy S25 Ultra 12GB 512GB", "Samsung") == ("galaxy s", 25)
    assert fam("Samsung 65-inch QLED 4K Smart TV", "Samsung") is None
    assert fam('Samsung 65" QLED 4K Smart TV', "Samsung") is None
    assert fam("Honeywell HCE200W Heater, 2-Pack", "Honeywell") is None  # a model code, not a generation
    assert fam("PlayStation 5 Slim 1TB", "Sony") == ("playstation", 5)
    assert fam("Nintendo Switch 2 System", "Nintendo") == fam("Nintendo Switch 2", "Nintendo") == ("switch", 2)
    assert fam("Nintendo Switch 2 2026 holiday deals", "Nintendo") == ("switch", 2)
    # Long titles from serpapi_cache (amazon_product B09XS7JWHH; ebay_product 178516729136 and two cached peers).
    assert fam("Sony WH-1000XM5 Premium Noise Cancelling Wireless Headphones, Black", "Sony") \
        == fam("Sony WH-1000XM5", "Sony") == ("wh-1000xm", 5)
    for t in ("Apple AirPods Pro 2nd Gen Earbuds w/ MagSafe Charging Case USB-C, White",
              "AirPods Pro 2nd Generation USB-C Magsafe Charging Case Wireless Earbuds",
              "Apple AirPods Pro 2nd Generation with MagSafe Wireless Charging Case (USB‑C)"):
        assert fam(t, "Apple") == fam("AirPods Pro 2", "Apple") == ("airpods pro", 2), t
    assert fam("Amazon Basics Neoprene Dumbbell Hand Weights for Exercise and Muscle Toning", "Amazon") is None
    assert fam("Honeywell HHF360V 360 Degree Surround Fan Forced Heater with Surround Heat Output", "Honeywell") is None
    assert fam("Honeywell 360 heater", "Honeywell") is None
    # Search terms for non-demo runs (resolve._generic): brand as Amazon/eBay give it; None -> first-3-words fallback.
    assert search_term("Motorola Sound MA1, the Only Google Authorized Wireless Android Auto Car Adapter, Smartphone to "
                       "Screen Wireless Bridge Car Accessories, USB Type-A", "Motorola Sound") == "Motorola MA1"
    assert search_term("Sony WH-1000XM5 Premium Noise Cancelling Wireless Headphones, Black", "Sony") == "Sony WH-1000XM5"
    assert search_term("Apple AirPods Pro 2nd Gen Earbuds w/ MagSafe Charging Case USB-C, White", "Apple") \
        == "Apple AirPods Pro 2"
    assert search_term("AirPods Pro 2nd Generation USB-C Magsafe Charging Case Wireless Earbuds", "AirPods") \
        == "AirPods Pro 2"  # eBay: no brand field, so resolve uses the title's first word
    assert search_term("Amazon Basics Neoprene Dumbbell Hand Weights for Exercise and Muscle Toning", "Amazon Basics") is None

    # 2. Candidates + release status on the three probed products (saved probe JSON, 0 searches).
    OUT = Path(__file__).parent.parent / "phase0" / "successor_probe" / "out"
    TODAY = date(2026, 9, 30)
    NAMES = {"sony-xm5": "Sony WH-1000XM5", "switch2": "Nintendo Switch 2", "honeywell-heater": "Honeywell 360 heater"}
    got, ARGS = {}, {}
    for k, name in NAMES.items():
        p = DEMO_PRODUCTS[k]
        load = lambda e: json.loads((OUT / f"{k}_{e}.json").read_text())
        term = p["trend_query"]
        args = (load("trends_rising")["related_queries"]["rising"] if names_product(term, name, p["brand"]) else None,
                [s["value"] for s in load("google_autocomplete").get("suggestions", [])], news_rows(load("google_news")))
        ARGS[k] = args
        r = got[k] = find_successor_candidates(name, *args, brand=p["brand"])
        print(f"\n=== {name}  (Trends term {term!r}: {'usable' if args[0] else 'UNUSABLE'})  candidates: "
              f"{r['status'].upper()}  usable sources: {r['sources_used']}")
        for c in r["candidates"]:
            s = c["release"] = classify_release_status(c, args[2], TODAY)
            print(f"  {c['name']} (sources {c['sources']}, trends {c['trends_value']}): {s['status'].upper()} since "
                  f"{s['since']}{', window ' + s['release_window'] if s['release_window'] else ''}")
            for e in s["evidence"]:
                print(f"     {e['date'][:10]}  {e['stage']:9} {e['source'][:16]:16} {e['title'][:90]}")
        for c in r["context"]:
            print(f"  context ({c['why']}): {c['text'][:100]}")

    sony = got["sony-xm5"]["candidates"]
    assert [c["identifier"] for c in sony] == [6] and sony[0]["name"] == "Sony WH-1000XM6"
    rel = sony[0]["release"]
    assert rel["status"] == "released" and rel["since"] == "2025-05-15", rel
    assert any(e["date"].startswith("2025-06-18") and e["stage"] == "released" for e in rel["evidence"])
    assert got["switch2"]["status"] == "none" and got["honeywell-heater"]["status"] == "unavailable"
    long = find_successor_candidates("Sony WH-1000XM5 Premium Noise Cancelling Wireless Headphones, Black",
                                     *ARGS["sony-xm5"], brand="Sony")
    assert [c["name"] for c in long["candidates"]] == ["Sony WH-1000XM6"]  # long title = clean name

    # 3. Candidate traps.
    news = lambda *titles: [{"title": t, "source": "x", "date": "2026-01-01T00:00:00Z", "link": ""} for t in titles]
    r = find_successor_candidates("AirPods Pro 2", [{"query": "airpod max 2", "value": "+1,150%"},
                                                    {"query": "skullcandy crusher anc 2", "value": "+1,100%"}],
                                  ["airpods pro 2 case", "galaxy buds pro 3"], news("Galaxy Buds Pro 3 review"),
                                  brand="Apple")
    assert r["status"] == "none", r  # competitors and accessories are never candidates
    r = find_successor_candidates("Sony WH-1000XM5", [{"query": "sony wh-1000xm4", "value": "+350%"}],
                                  ["sony wh-1000xm5 vs sony wh-1000xm6"], news("Sony WH-1000XM6 review"), brand="Sony")
    assert [c["identifier"] for c in r["candidates"]] == [6] and r["rejected"][0]["why"] == "older model"
    r = find_successor_candidates("Nintendo Switch 2", [{"query": "nintendo switch 2 2026 deals", "value": "+300%"}],
                                  ["nintendo switch 2 oled"],
                                  news("Updated Switch 2 Disassembled", "Spiritual successor to Wii Sports is out",
                                       "Nintendo Switch 3 rumors"), brand="Nintendo")
    assert r["status"] == "none" and any(c["why"].startswith("revision") for c in r["context"])
    r = find_successor_candidates("AirPods Pro 2", None, ["airpods pro 3rd gen"], news("AirPods Pro gen 3 is out"),
                                  brand="Apple")
    assert [c["identifier"] for c in r["candidates"]] == [3]
    assert find_successor_candidates("Neoprene dumbbell", ["a"], ["b"], news("c"))["status"] == "unavailable"

    # 4. Release-status date logic (synthetic).
    xm7 = {"name": "Sony WH-1000XM7", "family": "WH-1000XM", "identifier": 7}
    h = lambda t, d: {"title": t, "source": "x", "date": f"{d}T12:00:00Z", "link": ""}
    s = classify_release_status(xm7, [h("Sony announces WH-1000XM7, arriving November 2026", "2026-09-01")], TODAY)
    assert (s["status"], s["release_window"], s["release_date"], s["date_passed"]) == \
        ("announced", "November 2026", "2026-11-30", False), s
    s = classify_release_status(xm7, [h("Sony unveils the WH-1000XM7, due June 5", "2026-05-20")], TODAY)
    assert (s["status"], s["release_date"], s["date_passed"]) == ("announced", "2026-06-05", True), s
    s = classify_release_status(xm7, [h("Sony WH-1000XM7 leak shows new hinge", "2025-06-01")], TODAY)
    assert (s["status"], s["weak"]) == ("rumored (stale)", True), s
    s = classify_release_status(xm7, [h("Sony WH-1000XM7 could arrive this fall, report says", "2026-08-01")], TODAY)
    assert (s["status"], s["weak"]) == ("rumored", False), s
    s = classify_release_status(xm7, [h("Sony WH-1000XM7 review: quieter still", "2026-03-10"),
                                      h("Sony announces WH-1000XM7", "2026-01-15"),
                                      h("Sony WH-1000XM7 leaks", "2025-12-01")], TODAY)
    assert s["status"] == "released" and s["since"] == "2026-03-10" and [e["stage"] for e in s["evidence"]] == \
        ["released", "announced", "rumored"]  # later wins, and every stage stays in the evidence
    s = classify_release_status(xm7, [h("Sony WF-1000XM7 earbuds review", "2026-03-10"),   # different family
                                      h("Sony WH-1000XM7", "2026-03-10"),                  # names it, no stage words
                                      {**h("Sony announces WH-1000XM7", "2026-03-10"), "date": None}], TODAY)  # undated
    assert s["status"] == "none" and not s["evidence"], s
    s12 = {"name": "Samsung Galaxy Tab S12", "family": "Galaxy Tab S", "identifier": 12}  # real headlines, Prompt 4
    s = classify_release_status(s12, [
        h("Samsung Galaxy Tab S12 release date expectations, price estimates and upgrades", "2026-09-18"),
        h("Leak: Samsung Galaxy Tab S12 Ultra revealed with controversial design", "2026-07-16"),
        h("(VIDEO) Samsung Galaxy Tab S12 Ultra: Expected Release Date in Late 2026, Latest Rumors on Specs", "2026-02-21")],
        TODAY)
    assert s["status"] == "rumored" and {e["stage"] for e in s["evidence"]} == {"rumored"}, s
    ma2 = {"name": "Motorola MA2", "family": "MA", "identifier": 2}  # real headline, Prompt 4 option 3
    s = classify_release_status(ma2, [h("Motorola MA2 wireless Android Auto adapter quietly goes official, apparently "
                                        "costs $40", "2026-03-01")], TODAY)
    assert s["status"] == "announced", s
    # 5. Scoring: worked examples (lifecycle weight 0.25, as in timing.WEIGHTS).
    W = 0.25
    cand = lambda **rel: {"name": "Sony WH-1000XM7", "release": {"evidence": [], "date_passed": False, "release_date": None,
                                                                 "release_window": None, **rel}}
    ann = lambda d, w=None: cand(status="announced", release_date=d, release_window=w,
                                 evidence=[{**h("Sony announces WH-1000XM7", "2026-09-01"), "stage": "announced"}])
    assert score(ann("2026-10-20", "Oct 20"), TODAY)[0] == -1.0     # <= 30 days away
    assert score(ann("2026-11-30", "November 2026"), TODAY)[0] == -0.75
    assert score(ann(None), TODAY)[0] == -0.5                        # announced, no date in the headlines (MA2)
    assert score({**ann("2026-06-05"), "release": {**ann("2026-06-05")["release"], "date_passed": True}}, TODAY)[0] == 0
    # Borderline product (Switch 2 today: +0.136) -> an announcement due within 30 days moves it to WAIT...
    assert 0.136 + W * score(ann("2026-10-20"), TODAY)[0] < 0
    # ...but not a strongly positive price position alone (price +1.0 x 0.45 = +0.45, the rest neutral).
    assert 0.45 + W * -1.0 > 0
    rum = lambda *rows: cand(status="rumored", evidence=[{**h(t, d), "source": src, "stage": "rumored"} for t, d, src in rows])
    tab = rum(("S12 leak", "2026-09-18", "PhoneArena"), ("S12 leak", "2026-08-17", "91mobiles"),
              ("S12 leak", "2026-07-16", "notebookcheck"))  # 3 outlets, 3 months, newest 12 days old
    s_, why = score(tab, TODAY)
    assert s_ == RUMOR_SCORE and "rumored" in why and "2026-09-18" in why and "announced" not in why
    assert 0.04 + W * s_ < 0 < 0.136 + W * s_   # flips only a score within 0.05 of the line; Switch 2 (+0.136) stays buy
    assert score(rum(("S12 leak", "2026-09-18", "A"), ("S12 leak", "2026-09-10", "B")), TODAY)[0] == 0  # 2 outlets
    assert score(rum(*[("S12 leak", "2026-09-%02d" % d, s) for d, s in ((18, "A"), (10, "B"), (2, "C"))]), TODAY)[0] == 0
    assert score(rum(("S12 leak", "2026-05-18", "A"), ("S12 leak", "2026-04-10", "B"), ("S12 leak", "2026-03-02", "C")),
                 TODAY)[0] == 0  # newest is 135 days old: not recent
    assert score(cand(status="released", evidence=[{**h("Sony WH-1000XM7 review", "2026-03-10"), "stage": "released"}]),
                 TODAY) == (0.0, "A newer model, Sony WH-1000XM7, is already out “Sony WH-1000XM7 review” (x, 2026-03-10)")
    print("\nLIFECYCLE SELF-CHECK PASSED (0 searches)")
