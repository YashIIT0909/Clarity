"""Narrator agent: turns trust/timing structured signals into the human-readable reasoning chain (Groq)."""
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone

import httpx

import receipts

from events import EVENTS

MODEL = "openai/gpt-oss-120b"

SYSTEM = """You write the reasoning chain for Clarity, a shopping assistant that answers "should I buy this, right now, from here?".
Clarity checks trust first. If trust fails, it stops and says WALK AWAY, and timing is never analysed. If trust passes, it checks timing and says BUY NOW or WAIT.

Write for a shopper: plain, confident, specific. Use ONLY facts and numbers present in the input — never invent prices, dates, events, sources, or interpretations beyond the glossary below.
Every trust reason and every timing evidence fact must appear in some step, including ones that point against the final verdict. Each timing fact comes with a direction field — use exactly that direction, never reinterpret it, and say it in plain words ("which points to waiting"; a neutral direction: "which doesn't point either way"). Never drop a fact to make the story tidier.
Never write internal labels or field names: no "pulls toward", no direction tags in parentheses, no snake_case names, no JSON keys.
Each step is 1–2 short sentences. No hype, no emoji, no hedging filler.

main_reason — computed by Clarity from the scores: the one signal that contributed most to the verdict (name, its contribution, and its fact). For timing, name is demand (search interest), price (price position), maker (maker stock) or lifecycle (newer model); for a walk-away it is the trust flag that counted most. The headline names this reason and no other. The summary's "why" names it first and quotes its number. Never say another signal outweighs it, drives the verdict, or is the main reason. Say it in plain words from its fact: never write its name or its contribution number. If main_reason is null, name no main reason.

Tone matches confidence: if the confidence is below 0.60, the headline says it's a close call (e.g. "Buy now — close call: demand is rising"). Never use intensifiers (strongly, notably, significantly, sharply, far below…) unless the number they describe is 20% or more.

photo_and_store_evidence — what Clarity found about the listing's photo and, for screenshots, the shop:
- When it lists concerns, name the concrete evidence: which sites, what kind of site (the maker's own site, a big retailer, another marketplace listing, a forum), and prices when given. E.g. "It's listed as open box with signs of use, but its only photo is Apple's own product image, also used on apple.com and bestbuy.ca."
- Describe what the evidence shows and why it matters to a buyer. Never call a seller or shop a scam, scammer, fraud or fake, and never state or guess anyone's intent.
- If there are no concerns, write no photo step; at most say in the trust step that the photo check found nothing unusual.
- Anything under also_worth_knowing (e.g. whether a shop runs Google ads) is context only: never a reason for the verdict. You may add one short "Also worth knowing" sentence.

What the timing signals mean:
- Search interest trend / past-year pattern: rising demand ahead means prices are less likely to drop and stock may tighten (favours buying now); falling demand favours waiting.
- Price comparisons: a new listing is compared with new retail; a used/open-box/refurbished listing with other eBay listings in the same condition. Keep the trust reasons' own wording ("below new retail" vs "below the usual price for used units"); never compare a secondhand listing with new retail.
- Price vs retail median, cheaper retailers: paying above the market today favours waiting or buying elsewhere; at or below market favours buying.
- Maker stock trend: a weak, supporting signal only. A rising stock suggests the maker has little pressure to cut prices; a falling stock suggests discounts are more plausible. Never call it an endorsement.
- Retail events (Prime Day, Black Friday, ...) are never in your facts: code adds that context line after you write. Never mention events or sales, and never state or imply a price change or a discount.

- lifecycle: a newer model of this product found in searches and news, with its release status and the dated headlines behind it, or null. If null, never mention newer models or successors. Cite only a headline's title, source and date; never quote or invent anything beyond the headline. Never predict a price drop or its size, and never say the current model will get cheaper ("waiting means you can choose between both models at launch" is fine).
  - If decided_the_verdict is true, the other signals alone pointed to buying now and this newer model is why the verdict is wait: the headline and summary must name it as the main reason (e.g. "Wait — the Switch 3 is announced for November").
  - status "announced": write one step titled like "Newer model announced": the newer model's name, when and where it was announced (headline source and date), and its release_window if given, e.g. "Sony announced the WH-1000XM6 on <date>; it's due <window>." If there is no release_window, say the headlines give no release date. If release_date_passed, say the date has passed and no headline confirms it's out yet.
  - status "rumored" or "rumored (stale)": always use the word "rumored" or "leaked", give the newest headline's date, never write "announced" or "coming", and never state a date as fact. Write a step only if counted_toward_verdict is true; otherwise at most one "Also worth knowing" sentence.
  - status "released": not a reason for the verdict. In the final "Also worth knowing" step, one sentence naming the newer model and the dated headline showing it's out. Don't mention it in the headline or summary.

Return JSON exactly in this shape:
{
  "steps": [{"title": "<3-6 words, sentence case>", "text": "<1-2 sentences>"}],
  "headline": "<sentence case, <=10 words: the verdict plus its main reason, e.g. 'Wait — you are paying 24% over market'>",
  "summary": "<2 sentences: the decision and the single most important why. For buy/wait verdicts, state the confidence as a percentage.>"
}
Steps: one for the trust check; then, only if photo_and_store_evidence has concerns, one step for them right after it (title like "Photo evidence" or "Shop check"); then, only if trust passed, one for demand trend, one for price position, one for the maker signal (skip the maker step if no maker signal is given), and the lifecycle step as described above (only if lifecycle is not null). Then, only if lifecycle status is "released", ONE final step titled "Also worth knowing" holding that context sentence (code may add a retail-event line to it).
If trust failed, add one final step explaining that timing was deliberately skipped because a risky listing should not be bought at any price or time. If trust passed, do NOT write any step about skipping — never write a step for something that did not happen.
A walk-away verdict has no confidence figure; never state one for it.
If timing.judged is false, there was no demand, price or maker data to judge: write ONE timing step titled "Timing not judged" saying exactly "Nothing found suggests waiting, but we couldn't judge timing for this product" (instead of the demand/price/maker steps), state no confidence figure anywhere, and make the headline "Safe to buy — timing not judged"."""


def _evidence(t):
    """Photo/shop findings as their own fact block: concerns with concrete evidence, context kept apart."""
    s = t["signals"]
    concerns = lambda sigs: [
        {"finding": x["reason"], "evidence": [{k: e.get(k) for k in ("domain", "domain_class", "title", "price")}
                                              for e in x["evidence"][:6]]}
        for x in sigs or [] if x["status"] == "fired"]
    photo, store = s.get("photo") or {}, s.get("store") or {}
    f = photo.get("forensics")
    block = {"photo": {"checked": bool(f), "matches_found": f and f["total_display"],
                       "concerns": concerns(photo.get("signals"))}}
    if store.get("applies"):
        rep = store.get("reputation") or {}
        ads = rep.get("ads") or {}
        block["shop"] = {"domain": store["domain"], "web_footprint": rep.get("footprint"),
                         "concerns": concerns(store.get("signals")),
                         "also_worth_knowing": {"runs_google_ads": ads.get("runs_ads"),
                                                "ads_seen_since": ads.get("earliest_first_shown")}}
    return block


def _lifecycle(c):
    """Successor radar as a named fact: the newer model, its release status and the dated headlines behind it (title,
    source, date only). None when nothing was found or it couldn't run: then successors are never mentioned."""
    if not c or c["status"] in ("none", "unavailable"):
        return None
    top = next(x for x in c["candidates"] if x["name"] == c["successor"])
    rel = top["release"]
    return {"newer_model": top["name"], "status": rel["status"], "counted_toward_verdict": c["score"] != 0,
            "decided_the_verdict": c.get("decided_verdict", False),
            "status_since": rel["since"], "release_window": rel["release_window"], "release_date": rel["release_date"],
            "release_date_passed": rel["date_passed"],
            "headlines": [{k: e[k] for k in ("title", "source", "date", "stage")} for e in rel["evidence"][:5]]}


def _facts(result):
    t, tm = result["trust"], result["timing"]
    facts = {
        "final_verdict": result["final_verdict"],
        "listing": {k: t["signals"]["listing"].get(k) for k in ("title", "price", "seller", "marketplace", "condition")},
        "trust": {"verdict": t["verdict"], "reasons": t["reasons"]},
        "photo_and_store_evidence": _evidence(t),
        "main_reason": (tm or t).get("main_reason"),
    }
    if tm:
        demand = tm["signals"]["components"]["demand"]
        ev = demand.get("event")
        facts["timing"] = {
            "verdict": tm["verdict"], "confidence": tm["confidence"], "judged": tm["judged"],
            # the event has its own named fact below; drop its evidence line so it isn't narrated twice
            # the event and the successor have their own named facts below; drop their lines so they aren't narrated twice
            "evidence": [{"fact": e["fact"], "direction": e["pulls_toward"]} for e in tm["evidence"]
                         if not (ev and e["fact"].startswith(ev["name"])) and e["signal"] != "lifecycle"],
        }
        facts["lifecycle"] = _lifecycle(tm["signals"]["components"].get("lifecycle"))
        facts["upcoming_event"] = ev and {
            "name": ev["name"], "days_away": ev["days_away"], "search_term": demand["query"],
            "avg_search_interest_change_around_past_occurrences": ev.get("avg_interest_change"),  # None = not enough data
            "by_year": ev["interest_change_by_year"],
        }
    return facts


LABEL_IN_PARENS = re.compile(r"\s*\([^()]*\b(?:pull(?:s|ing)?|favou?rs?)\b[^()]*\)", re.I)
PULL = re.compile(r"\bpull(s|ing)?\s+towards?\b", re.I)


def _clean(text):
    """Last line of defence against internal direction labels (open decision #6): the prompt forbids them, this makes
    sure. "(pulls toward waiting)" is dropped; "which pulls toward waiting" becomes "which points to waiting"."""
    text = LABEL_IN_PARENS.sub("", text)
    return PULL.sub(lambda m: {"s": "points to", "ing": "pointing to"}.get((m.group(1) or "").lower(), "point to"), text)


ALSO = "Also worth knowing"
# Any retail event, by name or generically ("upcoming event", "the sale"): weight 0, so never a reason (open decision #13).
EVENT_WORDS = re.compile(r"\b(" + "|".join(map(re.escape, EVENTS)) + r")\b|\bevents?\b|\b(upcoming|holiday) sales?\b", re.I)


def _event_sentence(ev):
    """Rule-based "also worth knowing" line for the retail event (used when the LLM's own line had to be dropped)."""
    avg = ev["avg_search_interest_change_around_past_occurrences"]
    trend = ("there isn't enough past search data around it to compare" if avg is None else
             f"in past years search interest for \"{ev['search_term']}\" {'rose' if avg >= 0 else 'fell'} "
             f"{abs(avg) * 100:.0f}% on average around it")
    return (f"{ev['name']} starts in {ev['days_away']} days; {trend}. That's context only: it didn't count toward this "
            f"verdict, and it says nothing about whether prices will change.")


def _event_as_context_only(out, ev, verdict):
    """Last line of defence for #13, like _clean for label leaks: the retail event may appear only in the final "Also
    worth knowing" step. Headline -> plain verdict; summary sentences and other steps that mention it are dropped; the
    rule-based event line is added to "Also worth knowing" when the LLM didn't put it there."""
    if EVENT_WORDS.search(out["headline"]):
        out["headline"] = {"buy_now": "Buy now", "wait": "Wait", "walk_away": "Walk away"}.get(verdict, out["headline"])
    kept = [s for s in re.split(r"(?<=[.!?])\s+", out["summary"]) if not EVENT_WORDS.search(s)]
    out["summary"] = " ".join(kept) or out["summary"]
    steps = [s for s in out["steps"] if s["title"] == ALSO or not EVENT_WORDS.search(f"{s['title']} {s['text']}")]
    also = [s for s in steps if s["title"] == ALSO]
    rest = [s for s in steps if s["title"] != ALSO]
    text = " ".join(s["text"] for s in also)
    if not ev:  # no event in the facts: an "event" line would be invented
        text = " ".join(x for x in re.split(r"(?<=[.!?])\s+", text) if not EVENT_WORDS.search(x))
    elif not EVENT_WORDS.search(text):
        text = f"{text} {_event_sentence(ev)}".strip()
    out["steps"] = rest + ([{"title": ALSO, "text": text}] if text else [])
    return out


NOT_JUDGED_HEADLINE = "Safe to buy — timing not judged"
_not_judged = lambda result: bool(result["timing"]) and result["timing"]["judged"] is False


def _fallback(result):
    """Rule-based text if Groq is unreachable, so the demo never dead-ends on an LLM outage."""
    steps = [{"title": "Trust check", "text": "; ".join(result["trust"]["reasons"])}]
    if result["timing"]:  # the event line goes to "Also worth knowing" (_event_as_context_only), not the reasons
        steps.append({"title": "Timing check", "text": "; ".join(r for r in result["timing"]["reasons"] if not EVENT_WORDS.search(r))})
    else:
        steps.append({"title": "Timing skipped", "text": "Trust failed, so timing was not analysed."})
    v = NOT_JUDGED_HEADLINE if _not_judged(result) else result["final_verdict"].replace("_", " ").title()
    return {"steps": steps, "headline": v, "summary": steps[0]["text"], "fallback": True}


VERDICT_WORDS = {"buy_now": "Buy now", "wait": "Wait", "walk_away": "Walk away"}
SENTENCES = re.compile(r"(?<=[.!?])\s+")
# Words that name each reason, so a check can tell which reason a text names first. ponytail: keyword match; a
# paraphrase with none of these words counts as naming nothing, and the rule-based text takes over.
TIMING_NAMES = {
    "demand": r"search|demand|interest|popular|season",
    "price": r"price[sd]?\b|pricing|retail|market|median|cheaper|\$\d",
    "maker": r"maker|shares|(?-i:\b[A-Z]{3,5}\b) stock|stock (?:price|is|has|up|down|ris|rose|fell|fall|gain|climb|surg|jump)",
    "lifecycle": r"newer model|successor|announc|rumou?r|leak",
}
TRUST_NAMES = {
    "too_cheap": r"below|cheap|discount|too good|under (?:the )?(?:market|retail|median)",
    "too_expensive": r"above|overpriced|expensive",
    "no_returns": r"returns?\b",
    "unproven_seller": r"new seller|seller (?:has|with) (?:only|just|few|no|one|1)\b|feedback|track record|joined|unproven",
    "low_rating": r"rating|stars?\b",
    "sudden_drop": r"drop|cut|slash|lowered",
    "seller_pattern": r"other listings|same seller",
    "few_reviews": r"reviews?\b",
    "market_unknown": r"comparable|verify the price|market price",
    "photo": r"photo|image|picture",  # stock_photo_on_private_listing, stolen_own_photo, photo_on_scam_reports
    "store_complaints": r"complain|non-deliver|never arrived|didn't arrive",
    "no_web_footprint": r"footprint|online presence|little (?:is known|trace)",
}
# Short headline phrase per main reason, (buy, wait).
HEAD = {"demand": ("demand is rising", "demand is falling"), "price": ("price is below market", "price is above market"),
        "maker": ("maker's stock is rising", "maker's stock is falling"), "lifecycle": (None, "a newer model is {}")}


def _group(name):
    return "photo" if "photo" in name else name


def _named(text, names):
    """The reasons a text names, in order of first mention."""
    hits = {n: re.search(rx, text, re.I) for n, rx in names.items()}
    return sorted((n for n, m in hits.items() if m), key=lambda n: hits[n].start())


def _headline(result, facts, close_call=False):
    """Rule-based headline: the verdict plus its main reason (timing), or the plain verdict."""
    v, main = VERDICT_WORDS[result["final_verdict"]], facts.get("main_reason")
    phrase = None
    if main and main["name"] in HEAD:
        phrase = HEAD[main["name"]][result["final_verdict"] == "wait"]
        if main["name"] == "lifecycle":
            phrase = phrase.format("announced" if facts["lifecycle"]["status"] == "announced" else "rumored")
    return f"{v} — close call: {phrase}" if close_call and phrase else f"{v} — close call" if close_call else \
        f"{v} — {phrase}" if phrase else v


def _main_reason_only(out, result, facts):
    """Main reason is computed in code (timing.main_reason / trust flag with the most points): the headline may name no
    other reason, and the summary must name it first with its number. Otherwise -> rule-based text."""
    main = facts.get("main_reason")
    if not main:
        return out
    names = TIMING_NAMES if main["name"] in TIMING_NAMES else TRUST_NAMES
    want = _group(main["name"])
    if any(n != want for n in _named(out["headline"], names)):
        out["headline"] = _headline(result, facts)
    first = _named(out["summary"], names)[:1]
    pct = re.search(r"\d+%", main["text"])
    if first != [want] or (pct and pct.group() not in out["summary"]):
        conf = result["confidence"]
        v = VERDICT_WORDS[result["final_verdict"]]
        why = re.sub(r"\s*\([^()]*\)", "", SENTENCES.split(main["text"])[0]).rstrip(".")  # first sentence, no asides
        out["summary"] = f"{v}{f' ({conf:.0%} confidence)' if conf else ''}. Main reason: {why}."
    return out


WORD_NUMS = "zero one two three four five six seven eight nine ten eleven twelve".split()
DIRECTION = {"buy now": "points to buying now", "wait": "points to waiting", "neutral": "doesn't point either way"}
STEP_TITLE = {"demand": r"demand|interest|search", "price": r"price|retail", "maker": r"maker|stock",
              "lifecycle": r"model|successor"}
NEW_STEP = {"demand": "Demand trend", "price": "Price position", "maker": "Maker stock", "lifecycle": "Newer model"}


def _signature(fact):
    """Regex a narration must match for a fact to count as told: its first percentage (0% also as flat/unchanged),
    else its first dollar amount, else "N of M", else a "not enough data" phrase."""
    num = lambda n: rf"(?:{n}|{WORD_NUMS[int(n)]})" if int(n) < len(WORD_NUMS) else n
    if m := re.search(r"(\d+)%", fact):
        return rf"\b{m[1]} ?%|\b{m[1]} percent" + (r"|\bflat\b|unchanged|steady|stable|no change" if m[1] == "0" else "")
    if m := re.search(r"\$(\d+)", fact):
        return rf"\${m[1]}\b"
    if m := re.search(r"\b(\d+) of (\d+)\b", fact):
        return rf"\b{num(m[1])} of {num(m[2])}\b"
    return r"not enough|n't (?:judge|enough)|no (?:price|data)|couldn't|wasn't"


def _all_facts_told(out, result, facts):
    """Every scored timing fact, and the newer-model line, must appear in the narration. Each missing one gets its
    rule-based line appended to its signal's step (or a new step in demand/price/maker order, before "Also worth
    knowing"). The retail event line is already guaranteed by _event_as_context_only."""
    tm = result["timing"]
    if not tm or not tm["judged"]:
        return out
    ev = facts.get("upcoming_event")
    lc = facts.get("lifecycle")
    needed = [(e["signal"], e["fact"], e["pulls_toward"], _signature(e["fact"])) for e in tm["evidence"]
              if e["signal"] != "lifecycle" and not (ev and e["fact"].startswith(ev["name"]))]
    if lc:
        line = next((e["fact"] for e in tm["evidence"] if e["signal"] == "lifecycle"), None)
        if line:
            needed.append(("lifecycle" if lc["counted_toward_verdict"] else ALSO, line, None, re.escape(lc["newer_model"])))
    steps = [dict(s) for s in out["steps"]]
    order = list(NEW_STEP)
    for signal, fact, direction, sig in needed:
        if re.search(sig, " ".join(s["text"] for s in steps) + " " + out["summary"], re.I):
            continue
        line = fact[0].upper() + fact[1:] + (f", which {DIRECTION[direction]}." if direction else ".")
        if signal == ALSO:
            i = next((i for i, s in enumerate(steps) if s["title"] == ALSO), None)
            if i is None:
                steps.append({"title": ALSO, "text": ""})
                i = len(steps) - 1
        else:
            i = next((i for i, s in enumerate(steps) if re.search(STEP_TITLE[signal], s["title"], re.I)), None)
            if i is None:  # new step before the first later signal's step, or before "Also worth knowing"
                later = [re.compile(STEP_TITLE[k], re.I) for k in order[order.index(signal) + 1:]]
                i = next((i for i, s in enumerate(steps) if s["title"] == ALSO or any(r.search(s["title"]) for r in later)),
                         len(steps))
                steps.insert(i, {"title": NEW_STEP[signal], "text": ""})
        steps[i]["text"] = f"{steps[i]['text']} {line}".strip()
    return {**out, "steps": steps}


# Intensifiers need a number that earns them: 20%+ in the same sentence.
INTENSIFIERS = ("strongly", "strong", "notably", "notable", "significantly", "significant", "substantially",
                "substantial", "dramatically", "dramatic", "sharply", "sharp", "considerably", "considerable",
                "markedly", "remarkably", "hugely", "huge", "massively", "massive")
BIG_ENOUGH = 20
INTENSE = re.compile(r"\b(?:" + "|".join(INTENSIFIERS) + r")\b,?\s*|\bfar (?=below|above|more|less|cheaper|higher|lower)", re.I)


def _calm(text):
    out = []
    for s in SENTENCES.split(text):
        if not any(int(p) >= BIG_ENOUGH for p in re.findall(r"(\d+) ?%", s)):
            s = INTENSE.sub("", s)
            s = s[:1].upper() + s[1:]
        out.append(s)
    return " ".join(out)


def _tone(out, result, facts):
    """Tone matches confidence (buy/wait only): below 60% the headline says it's a close call; intensifiers only
    where a 20%+ number backs them."""
    conf = result["confidence"]
    if conf is None:
        return out
    headline = _calm(out["headline"])
    if conf < 0.6 and "close call" not in headline.lower():
        headline = _headline(result, facts, close_call=True)
    return {**out, "headline": headline, "summary": _calm(out["summary"]),
            "steps": [{**s, "text": _calm(s["text"])} for s in out["steps"]]}


NARRATOR_USAGE = []  # Groq tokens per live narrator call (verdict and "What owners say"), for budget reports


def _chat(engine, system, user):
    """One narrator Groq call, cached in serpapi_cache keyed by the exact input (model, prompt and the facts it is
    given), so replaying a demo chip costs no Groq tokens; demo_fixtures pins the rows a replay reads. Facts that
    change (an event's days-away, a new price) make a new key: that narration is written fresh."""
    key = {"input": hashlib.sha256(f"{MODEL}\n{system}\n{user}".encode()).hexdigest()}
    try:
        import serpapi
        hit = serpapi.cached(engine, key)
    except Exception:  # noqa: BLE001 — no cache is slower, not broken
        serpapi, hit = None, None
    if hit:
        return hit["reply"]
    for attempt in range(4):  # a per-minute 429 waits out retry-after (<= 30 s); a per-day one fails over to the fallback
        r = httpx.post("https://api.groq.com/openai/v1/chat/completions",
                       headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
                       json={"model": MODEL, "temperature": 0.2, "reasoning_effort": "low",
                             "response_format": {"type": "json_object"},
                             "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                       timeout=30)
        wait = float(r.headers.get("retry-after") or 5)
        if r.status_code != 429 or "per day" in r.text or wait > 30 or attempt == 3:
            break
        time.sleep(wait)
    r.raise_for_status()
    NARRATOR_USAGE.append((r.json().get("usage") or {}).get("total_tokens") or 0)
    reply = r.json()["choices"][0]["message"]["content"]
    if serpapi:
        try:
            serpapi.db.table("serpapi_cache").upsert(
                {"engine": engine, "query_params_hash": serpapi._hash(engine, key), "response_json": {"reply": reply},
                 "fetched_at": datetime.now(timezone.utc).isoformat()}, on_conflict="engine,query_params_hash").execute()
        except Exception:  # noqa: BLE001
            pass
    return reply


HISTORY_COUNT = re.compile(r"across \d+ earlier checks")


def _stable_input(facts):
    """What Groq sees: the facts minus anything that changes with the clock or the run, so a chip's cached narration
    is reused on any day. The retail event (days away, which event is next) and the successor's "date passed" flag are
    left out (code inserts the event line after generation: _event_as_context_only); trust's "across 13 earlier checks"
    count grows every run, so the number is dropped."""
    out = {k: v for k, v in facts.items() if k != "upcoming_event"}
    out["trust"] = {**facts["trust"], "reasons": [HISTORY_COUNT.sub("across earlier checks", r) for r in facts["trust"]["reasons"]]}
    if facts.get("lifecycle"):
        out["lifecycle"] = {k: v for k, v in facts["lifecycle"].items() if k != "release_date_passed"}
    return out


def run(result: dict) -> dict:
    """result: orchestrator output with trust/timing. Returns {steps, headline, summary}."""
    facts = _facts(result)
    try:
        out = json.loads(_chat("groq_narrator", SYSTEM, json.dumps(_stable_input(facts), default=str)))
        assert out.get("steps") and out.get("headline") and out.get("summary")
        for step in out["steps"]:
            step["text"] = _clean(step["text"])
        out = {**out, "headline": _clean(out["headline"]), "summary": _clean(out["summary"])}
    except Exception as e:  # noqa: BLE001 — any LLM failure degrades to rule-based text
        out = {**_fallback(result), "error": str(e)[:200]}
    out = _event_as_context_only(out, facts.get("upcoming_event"), result["final_verdict"])
    # after the event guard: it drops whole steps that mention the event, which can take a scored fact with them
    out = _tone(_all_facts_told(_main_reason_only(out, result, facts), result, facts), result, facts)
    return {**out, "headline": NOT_JUDGED_HEADLINE} if _not_judged(result) else out


# --- "What owners say": a separate step after the verdict, never part of its reasoning ------------------------------
OWNERS_TITLE = "What owners say"
NUM_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
GENERALIZE = re.compile(r"\b(most|many|all|every|lots of|plenty of|countless) (owners|users|people|buyers|customers|reviewers)\b"
                        r"|\bowners (hate|love)\b|\bbreaks? (often|easily|a lot)\b|\d+\s?%|\bpercent\b"
                        r"|\b(defect|failure|return|breakage|complaint) rates?\b"
                        r"|\bout of \d+\b|\balways\b|\bnever works\b|\bnotorious", re.I)
DEFECT_WORDS = re.compile(r"\b(defects?|defective|faults?|faulty|failures?|fails?|breaks?|broken|problems?)\b", re.I)
VERDICT_TALK = re.compile(r"\b(buy now|walk away|wait for|verdict|confidence)\b", re.I)
QUOTED = re.compile(r"[\"“]([^\"”]{3,})[\"”]")
OWNERS_LINES = {
    "none": "Independent reviews don't show a recurring problem.",
    "limit": "What owners say isn't available right now: Groq's free-tier limit was reached. Try again in a while.",
    "down": "What owners say isn't available right now: the language model couldn't be reached. Try again in a while.",
    "on_request": "Owner reviews haven't been checked for this product yet. “See what owners say” searches "
                  "review videos and forums live (about 2–3 minutes).",
}
OWNERS_SYSTEM = """You write the "What owners say" step for Clarity, a shopping assistant, from the JSON facts given.
It comes after the verdict and is never part of it: don't mention the verdict, buying advice, prices to pay or timing.
Rules:
- Mention only the items given, each with its source count worded from "sources" ("Two reviewers and one forum thread
  mention ..."). Paraphrase the label; don't add details that aren't in the facts.
- type "criticism" is a design choice or a price point: call it a common criticism, never a problem, defect or failure.
  type "problem" is something failing.
- type "concern" is a risk reviewers raise without reporting it happening: say they flag it as a possible risk
  ("Three reviewers flag Joy-Con drift as a possible risk"); never state that it happens.
- forum_reported items: say they are "reported by owners in forums" and keep them separate from what reviewers say.
- If there is praise as well as issues, include both.
- Quotes: use only the quotes given, each at most once, copied exactly, in double quotes, attributed to its channel
  and timestamp, e.g. Marques Brownlee at 2:15: "...". Everything else is paraphrased.
- Never generalize ("owners hate it", "it breaks often", "most people") and never state a rate or a percentage.
- 2 to 4 short, calm sentences.
- If previous_text and broken_rule are given, your earlier text broke that rule: rewrite it so it follows every rule.
Return JSON {"text": "..."}"""


def _n(k):
    return NUM_WORDS[k] if k < len(NUM_WORDS) else str(k)


def _sources_phrase(reviewers, threads):
    parts = [f"{_n(reviewers)} reviewer{'s' * (reviewers != 1)}"] if reviewers else []
    parts += [f"{_n(threads)} forum thread{'s' * (threads != 1)}"] if threads else []
    p = " and ".join(parts)
    return p[:1].upper() + p[1:]


def _label(label):
    return receipts.display_label(label)


def _mid(label):
    """A display label inside a sentence: "high comfort", but "Joy-Con drift" / "LCD vs OLED" keep their acronyms."""
    first = label.split(" ", 1)[0]
    return label if first in receipts.ACRONYMS.values() else label[:1].lower() + label[1:]


def _mmss(s):
    return f"{s // 60}:{s % 60:02d}"


def owners_facts(r: dict, name: str) -> dict:
    """The facts the step may use: known items with their source counts and type, forum-reported items, and at most
    one quote per video (its shortest, under 15 words), plus what was missing when unavailable."""
    items, quotes, seen = [], [], set()
    for key in ("known_issues", "known_praise"):
        for c in r.get(key) or []:
            vids = {s["channel"] for s in c["sources"] if s["type"] == "video" and s.get("model", "confirmed") == "confirmed"}
            threads = sum(s["type"] == "forum" for s in c["sources"])
            kind = c.get("type") or receipts.issue_type(c["kind"], c["label"])
            items.append({"type": kind, "label": _label(c["label"]), "sources": _sources_phrase(len(vids), threads),
                          "also_in_marketplace_reviews": bool(c.get("marketplace"))})
            for s in sorted((s for s in c["sources"] if s["type"] == "video"), key=lambda s: len(s["quote"].split())):
                if s["video_id"] not in seen and len(s["quote"].split()) < 15:
                    seen.add(s["video_id"])
                    quotes.append({"channel": s["channel"], "timestamp": _mmss(s["timestamp_s"]), "quote": s["quote"],
                                   "for": _label(c["label"])})
                    break
    forum = [{"label": _label(c["label"]), "threads": c["thread_count"], "sites": c["sites"]}
             for c in r.get("forum_reported") or []]
    return {"product": name, "status": r.get("status"), "items": items, "forum_reported": forum, "quotes": quotes,
            "missing": _missing(r, name)}


def _missing(r, name):
    """Which source was missing, in plain words (for "unavailable")."""
    why = r.get("why") or ""
    if "no model" in why:
        return ["the product name has no model to match reviews on"]
    out, eng = [], r.get("engines") or {}
    if str(eng.get("youtube", "ok")).startswith("unavailable"):
        out.append("the YouTube search failed")
    tr = [v for k, v in eng.items() if k.startswith("youtube_video_transcript")]
    failed = sum(v != "ok" for v in tr)
    if failed:
        out.append("the review videos' transcripts couldn't be fetched" if failed == len(tr) else
                   "some video transcripts couldn't be fetched")
    if str(eng.get("google_forums", "ok")).startswith("unavailable"):
        out.append("the forum search failed")
    a = r.get("audit") or {}
    if "confirmed_videos" in a and not a["confirmed_videos"] and not (tr and failed == len(tr)):
        out.append(f"no review video could be confirmed as the {name}" if a.get("videos")
                   else "no usable review videos were found")
    if "threads_kept" in a and not a["threads_kept"] and "the forum search failed" not in out:
        out.append("no forum threads name it")
    return out or [why or "the sources couldn't be read"]


def owners_fallback(facts: dict) -> str:
    """Rule-based "What owners say": always available, uses only the facts."""
    st = facts["status"]
    if st == "none":
        return OWNERS_LINES["none"]
    if st == "on_request":
        return OWNERS_LINES["on_request"]
    if st != "found":
        return OWNERS_LINES[facts["limit"]] if facts.get("limit") else \
            "We couldn't check what owners say: " + "; ".join(facts["missing"]) + "."
    lines = []
    for it in facts["items"]:
        if it["type"] == "problem":
            lines.append(f"{it['sources']} mention {_mid(it['label'])}.")
        elif it["type"] == "criticism":
            lines.append(f"{it['sources']} share a common criticism: {_mid(it['label'])}.")
        elif it["type"] == "concern":
            lines.append(f"{it['sources']} flag {_mid(it['label'])} as a possible risk.")
        else:
            lines.append(f"{it['sources']} praise {_mid(it['label'])}.")
    lines += [f"Reported by owners in forums ({_n(f['threads'])} threads on {_n(f['sites'])} sites): {_mid(f['label'])}."
              for f in facts["forum_reported"]]
    q = facts["quotes"][:1]
    lines += [f"{x['channel']} at {x['timestamp']}: “{x['quote']}”" for x in q]
    return " ".join(lines)


def owners_check(text: str, facts: dict) -> str | None:
    """Why a generated "What owners say" breaks the rules, or None. Checked in code, never trusted to the prompt."""
    norm = lambda s: re.sub(r"[^\w\s]", "", s.lower()).split()
    given = {" ".join(norm(q["quote"])): q for q in facts["quotes"]}
    used = []
    for m in QUOTED.findall(text):
        q = given.get(" ".join(norm(m)))
        if not q:
            return f"quote not from the transcripts: {m[:40]!r}"
        if len(m.split()) >= 15:
            return "quote of 15+ words"
        if q["channel"] in used:
            return f"two quotes from {q['channel']}"
        if q["channel"] not in text or q["timestamp"] not in text:
            return f"quote not attributed to {q['channel']} at {q['timestamp']}"
        used.append(q["channel"])
    own = QUOTED.sub(" ", text)  # the paraphrase only: a reviewer's own words may say "84%"
    if GENERALIZE.search(own):
        return f"generalizes or states a rate: {GENERALIZE.search(own).group()!r}"
    if VERDICT_TALK.search(own):
        return "talks about the verdict"
    if not any(i["type"] == "problem" for i in facts["items"]) and DEFECT_WORDS.search(own):
        return f"calls a criticism a {DEFECT_WORDS.search(own).group()!r}"
    if any(i["type"] == "criticism" for i in facts["items"]) and "criticism" not in own.lower():
        return "doesn't word the design/price items as a common criticism"
    sentences = [x.lower() for x in re.split(r"(?<=[.!?])\s+", own)]
    for it in (i for i in facts["items"] if i["type"] == "criticism"):  # each one, not just once per text
        words = [w for w in re.findall(r"[a-z0-9-]{4,}", it["label"].lower()) if w not in ("with", "than", "that")]
        said = [x for x in sentences if any(w in x for w in words)]
        if said and not any("criticism" in x for x in said):
            return f"doesn't word {it['label']!r} as a common criticism"
    if any(i["type"] == "concern" for i in facts["items"]) and "possible risk" not in own.lower():
        return "doesn't word the reviewers' concerns as a possible risk"
    if facts["forum_reported"] and "reported by owners in forums" not in own.lower():
        return "forum-reported items not labelled as such"
    praise = [i for i in facts["items"] if i["type"] == "praise"]
    words = {w for i in praise for w in re.findall(r"[a-z]{4,}", i["label"].lower())}
    if praise and len(praise) < len(facts["items"]) and not any(w in own.lower() for w in words):
        return "issues without the known praise"
    return None


def owners(receipts_result: dict, name: str, llm=None) -> dict:
    """The "What owners say" step: {title, text, fallback, error?}. Groq writes it only when there is something to
    say (status found); its text must pass owners_check, else the rule-based text is used. none / unavailable /
    on_request always get their fixed calm line (no tokens)."""
    facts = owners_facts(receipts_result or {"status": "unavailable"}, name)
    rr = receipts_result or {}
    facts["limit"] = rr.get("retry") and ("limit" if "limit" in (rr.get("why") or "") else "down")
    if facts["status"] != "found":
        return {"title": OWNERS_TITLE, "text": owners_fallback(facts), "fallback": True}
    llm = llm or _owners_llm
    try:
        text = llm(facts)
        why = owners_check(text, facts)
        if why:  # one retry, told exactly which rule it broke; then the rule-based text
            text = llm({**facts, "previous_text": text, "broken_rule": why})
            why = owners_check(text, facts)
            if why:
                raise ValueError(why)
        return {"title": OWNERS_TITLE, "text": text.strip(), "fallback": False}
    except Exception as e:  # noqa: BLE001 — any LLM failure or rule break degrades to rule-based text
        return {"title": OWNERS_TITLE, "text": owners_fallback(facts), "fallback": True,
                "error": f"{type(e).__name__}: {e}"[:200]}


def _owners_llm(facts):
    user = json.dumps({k: v for k, v in facts.items() if k not in ("missing", "limit")})
    return json.loads(_chat("groq_owners", OWNERS_SYSTEM, user))["text"]


if __name__ == "__main__":
    assert _clean("Interest fell 23% (pulls toward waiting).") == "Interest fell 23%."
    assert _clean("Interest fell 23%, which pulls toward waiting.") == "Interest fell 23%, which points to waiting."
    assert _clean("Stock is up, pulling toward buying now.") == "Stock is up, pointing to buying now."
    assert _clean("Price is 10% above retail (so wait).") == "Price is 10% above retail (so wait)."
    # #13: the retail event is never a reason. The heater's real 2026-09-30 Groq output, before the fix:
    ev = {"name": "Prime Big Deal Days", "days_away": 6, "search_term": "space heater",
          "avg_search_interest_change_around_past_occurrences": 1.27}
    out = _event_as_context_only({
        "headline": "Buy — maker stock strong",
        "summary": "Buy now with 62% confidence. Although the price is slightly above the retail median, the strong rise "
                   "in HELE stock and upcoming event suggest it's a good time to purchase.",
        "steps": [{"title": "Maker stock", "text": "HELE stock is up 93% over 6 months, which points to buying now."},
                  {"title": "Upcoming event", "text": "Prime Big Deal Days is 6 days away and search interest has "
                                                      "historically risen about 127% around this event."}]}, ev, "buy_now")
    assert out["summary"] == "Buy now with 62% confidence." and [s["title"] for s in out["steps"]] == ["Maker stock", ALSO]
    assert "Prime Big Deal Days starts in 6 days" in out["steps"][-1]["text"] and "context only" in out["steps"][-1]["text"]
    assert _event_as_context_only({"headline": "Wait — Black Friday is close", "summary": "Wait.", "steps": []},
                                  None, "wait") == {"headline": "Wait", "summary": "Wait.", "steps": []}
    kept = _event_as_context_only({"headline": "Buy now", "summary": "Buy now.", "steps": [
        {"title": ALSO, "text": "Prime Big Deal Days starts in 6 days; interest rose 10% around it."}]}, ev, "buy_now")
    assert kept["steps"] == [{"title": ALSO, "text": "Prime Big Deal Days starts in 6 days; interest rose 10% around it."}]

    # Main reason is computed, not narrated. Heater, 2026-09-30: demand +0.20 (0.5 x 0.40) > maker +0.15 > price -0.11.
    heater = {"final_verdict": "buy_now", "confidence": 0.62, "timing": {"judged": True, "evidence": [
        {"signal": "demand", "fact": "Search interest up 0% over the last 4 weeks vs the 8 before", "pulls_toward": "neutral"},
        {"signal": "demand", "fact": "In past years (2 yrs), interest rose 150% over the 8 weeks after this point",
         "pulls_toward": "buy now"},
        {"signal": "price", "fact": "$41.99 is 8% above the retail median of $38.77", "pulls_toward": "wait"},
        {"signal": "price", "fact": "1 of 2 retailers on the product page are cheaper right now", "pulls_toward": "wait"},
        {"signal": "maker", "fact": "HELE stock up 93% over 6 months", "pulls_toward": "buy now"}]}}
    hf = {"main_reason": {"name": "demand", "contribution": 0.2, "text": heater["timing"]["evidence"][1]["fact"]},
          "upcoming_event": None, "lifecycle": None}
    llm = {"headline": "Buy — maker stock strong", "steps": [],
           "summary": "Buy now with 62% confidence. Although the price is slightly above the retail median, the strong "
                      "rise in HELE stock suggests it's a good time to purchase."}
    fixed = _main_reason_only(dict(llm), heater, hf)
    assert fixed["headline"] == "Buy now — demand is rising", fixed
    assert fixed["summary"] == ("Buy now (62% confidence). Main reason: In past years, interest rose 150% over "
                                "the 8 weeks after this point."), fixed
    good = {"headline": "Buy now — demand usually rises from here", "steps": [],
            "summary": "Buy now with 62% confidence. In past years search interest rose 150% over the next 8 weeks, "
                       "more than the 8% price premium."}
    assert _main_reason_only(dict(good), heater, hf) == good
    assert _named("HELE stock is up, but demand", TIMING_NAMES) == ["maker", "demand"]

    # Dropped facts: the demand step lost the past-year line and there is no maker step -> both restored in place.
    told = _all_facts_told({"headline": "h", "summary": "s", "steps": [
        {"title": "Demand trend", "text": "Search interest is flat over the last 4 weeks."},
        {"title": "Price position", "text": "$41.99 is 8% above the median; one of two retailers is cheaper."},
        {"title": ALSO, "text": "x"}]}, heater, hf)["steps"]
    assert told[0]["text"].endswith("In past years (2 yrs), interest rose 150% over the 8 weeks after this point, "
                                    "which points to buying now."), told[0]
    assert [s["title"] for s in told] == ["Demand trend", "Price position", "Maker stock", ALSO]
    assert told[2]["text"] == "HELE stock up 93% over 6 months, which points to buying now."

    # Tone: no intensifier on a 9% gap, kept on 93%; below 60% the headline is a close call.
    assert _calm("The price is notably 9% below market, strongly favouring buying.") == \
        "The price is 9% below market, favouring buying."
    assert _calm("Strongly rising demand. HELE stock rose sharply, up 93%.") == "Rising demand. HELE stock rose sharply, up 93%."
    assert _calm("It's far below the median.") == "It's below the median."
    close = _tone({"headline": "Buy now — demand is rising", "summary": "", "steps": []},
                  {**heater, "confidence": 0.57}, hf)
    assert close["headline"] == "Buy now — close call: demand is rising", close
    assert _tone({"headline": "Buy now — a close call", "summary": "", "steps": []},
                 {**heater, "confidence": 0.57}, hf)["headline"] == "Buy now — a close call"
    print("narrator checks passed")
    # "What owners say": rule-based text on the real saved summaries (phase0, 20b verifier run), 0 Groq calls.
    from pathlib import Path
    V2 = Path(__file__).parent.parent / "phase0" / "receipts_probe" / "out" / "points_v2"
    summ = {k: json.loads((V2 / f"summary_{k}_gpt-oss-20b.json").read_text()) for k in ("sony-xm5", "switch2", "honeywell-heater")}
    httpx.post = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("no LLM in this check"))
    sony_f = owners_facts(summ["sony-xm5"], "Sony WH-1000XM5")
    types = {i["label"]: i["type"] for i in sony_f["items"]}
    assert types["Non-foldable design"] == types["No USB-C audio"] == "criticism" and types["High comfort"] == "praise", types
    assert len({q["channel"] for q in sony_f["quotes"]}) == len(sony_f["quotes"]) and all(
        len(q["quote"].split()) < 15 for q in sony_f["quotes"])  # one per video, under 15 words
    sony_txt = owners(summ["sony-xm5"], "Sony WH-1000XM5")
    assert sony_txt["fallback"] and sony_txt["error"]  # any LLM failure (no key, unreachable) -> rule-based text
    assert "share a common criticism: non-foldable design" in sony_txt["text"] and "praise high comfort" in sony_txt["text"]
    assert owners_check(sony_txt["text"], sony_f) is None, owners_check(sony_txt["text"], sony_f)  # fallback obeys the rules
    sw_f = owners_facts(summ["switch2"], "Nintendo Switch 2")
    sw = {i["label"]: i for i in sw_f["items"]}
    assert sw["Joy-Con drift"]["type"] == "problem" and sw["Short battery life"]["type"] == "criticism"
    sw_txt = owners_fallback(sw_f)
    assert "mention Joy-Con drift" in sw_txt and "share a common criticism: LCD vs OLED" in sw_txt and \
        owners_check(sw_txt, sw_f) is None, sw_txt
    heater_txt = owners(summ["honeywell-heater"], "Honeywell 360 (HHF360V)")["text"]
    assert heater_txt == ("We couldn't check what owners say: no review video could be confirmed as the Honeywell 360 "
                          "(HHF360V); no forum threads name it."), heater_txt
    assert owners({"status": "none"}, "X")["text"] == OWNERS_LINES["none"]
    assert owners({"status": "on_request"}, "X")["text"] == OWNERS_LINES["on_request"]
    assert owners({"status": "unavailable", "retry": True, "why": "Groq's free-tier limit was reached: try again later"},
                  "X")["text"] == OWNERS_LINES["limit"]
    assert owners({"status": "unavailable", "retry": True, "why": "the language model couldn't be reached"},
                  "X")["text"] == OWNERS_LINES["down"]
    assert owners({"status": "unavailable", "engines": {"youtube": "unavailable (TimeoutError)", "google_forums":
                   "unavailable (TimeoutError)"}, "audit": {}}, "X")["text"] == \
        "We couldn't check what owners say: the YouTube search failed; the forum search failed."
    all_failed = {"status": "unavailable", "engines": {"youtube": "ok", "google_forums": "ok",
                  **{f"youtube_video_transcript v{i}": "unavailable (RuntimeError)" for i in range(3)}},
                  "audit": {"confirmed_videos": 0, "videos": 0, "threads_kept": 8}}
    assert owners(all_failed, "X")["text"] == "We couldn't check what owners say: the review videos' transcripts couldn't be fetched."
    assert owners({"status": "unavailable", "why": "no model to match review videos and forums on"}, "X")["text"].endswith(
        "the product name has no model to match reviews on.")
    hinge = {"status": "found", "known_issues": [], "known_praise": [], "forum_reported": [
        {"label": "headband breaks", "thread_count": 3, "sites": 2, "sources": []}]}
    assert owners(hinge, "Sony")["text"] == \
        "Reported by owners in forums (three threads on two sites): headband breaks."  # never merged with reviewers
    # Generated text must pass owners_check, else the rule-based text is used.
    q = sony_f["quotes"][0]
    good = (f"Two reviewers share a common criticism: the headphones don't fold, so the case is bulky. Three reviewers "
            f"praise the comfort. {q['channel']} at {q['timestamp']}: \"{q['quote']}\"")
    assert owners_check(good, sony_f) is None
    assert not owners(summ["sony-xm5"], "Sony WH-1000XM5", llm=lambda f: good)["fallback"]
    bad = {
        "quote not from the transcripts": good.replace(q["quote"], "these headphones are the best ever made"),
        "two quotes from": good + f' {q["channel"]} at {q["timestamp"]}: "{q["quote"]}"',
        "quote not attributed": good.replace(f"{q['channel']} at {q['timestamp']}: ", ""),
        "generalizes": good.replace("Two reviewers", "Most owners"),
        "generalizes ": good + " About 12% of units fail.",
        "talks about the verdict": good + " That's why we say buy now.",
        "calls a criticism": good.replace("a common criticism", "a defect"),
        "common criticism": good.replace("share a common criticism", "dislike"),
        "without the known praise": "Two reviewers share a common criticism: no USB-C audio.",
    }
    for want, text in bad.items():
        out = owners(summ["sony-xm5"], "Sony WH-1000XM5", llm=lambda f, t=text: t)
        assert out["fallback"] and want.strip() in out["error"], (want, out)
    # Concerns: worded as a possible risk, never as happening.
    concern = {"status": "found", "known_issues": [{"kind": "issue", "type": "concern", "label": "joy‑con drift issues",
               "sources": [{"type": "video", "channel": c, "video_id": c, "timestamp_s": 60, "quote": "q", "model": "confirmed"}
                           for c in ("A", "B", "C")]}], "known_praise": [], "forum_reported": []}
    cf = owners_facts(concern, "Nintendo Switch 2")
    assert owners_fallback(cf).startswith("Three reviewers flag Joy-Con drift as a possible risk.")
    assert owners_check("Three reviewers report Joy-Con drift.", cf) == "doesn't word the reviewers' concerns as a possible risk"
    assert owners_check("Three reviewers flag Joy-Con drift as a possible risk.", cf) is None
    # One retry, told the broken rule; then the fallback.
    seen = []
    def retry_llm(f):
        seen.append(f.get("broken_rule"))
        return "Three reviewers report Joy-Con drift." if len(seen) == 1 else "Three reviewers flag Joy-Con drift as a possible risk."
    out = owners(concern, "Nintendo Switch 2", llm=retry_llm)
    assert not out["fallback"] and seen == [None, "doesn't word the reviewers' concerns as a possible risk"], (out, seen)
    out = owners(concern, "Nintendo Switch 2", llm=lambda f: "Three reviewers report Joy-Con drift.")
    assert out["fallback"] and out["text"].startswith("Three reviewers flag Joy-Con drift as a possible risk.")
    # Each criticism item, not just one: Sony's real 2026-10-01 text said "criticize weaker transparency".
    crit = {"status": "found", "known_issues": [
        {"kind": "issue", "type": "criticism", "label": l, "sources": [{"type": "video", "channel": c, "video_id": c,
         "timestamp_s": 1, "quote": "q", "model": "confirmed"} for c in ("A", "B")]} for l in ("non-foldable design", "weaker transparency")],
        "known_praise": [], "forum_reported": []}
    crf = owners_facts(crit, "Sony WH-1000XM5")
    assert owners_check("Two reviewers note the non-foldable design as a common criticism. Two reviewers criticize "
                        "weaker transparency.", crf) == "doesn't word 'Weaker transparency' as a common criticism"
    assert owners_check("Two reviewers share common criticisms: the non-foldable design and weaker transparency.", crf) is None
    long_q = {"channel": "CNET", "timestamp": "1:00", "quote": " ".join(["word"] * 15), "for": "x"}
    assert owners_check(f'CNET at 1:00: "{long_q["quote"]}"', {**sony_f, "quotes": [long_q]}) == "quote of 15+ words"
    # 84% inside a reviewer's own quote is their words, not a rate we claim.
    q84 = {**sony_f, "quotes": [{"channel": "SoundGuys", "timestamp": "1:58", "quote": "reduce the loudness of outside Noise by about 84%", "for": "x"}]}
    assert owners_check(good.split(q["channel"])[0] + 'SoundGuys at 1:58: "reduce the loudness of outside Noise by about 84%"', q84) is None
    print("owners narration checks passed (0 Groq calls)")

