"""Owner Receipts: what independent reviewers and owners say about this product, with receipts (video timestamps,
thread links). Context only: weight 0, never moves a verdict. No SerpApi calls here: callers pass the responses.

1. review_target: what a video title must name. Generation products (Sony WH-1000XM5, Nintendo Switch 2) reuse
   lifecycle.parse_model: brand + family + exactly this identifier. Model-code products (Honeywell HHF360V) match
   brand + common name ("Honeywell 360", derived from the code and the listing title) or the exact code.
2. select_review_videos: strict, auditable title rules; every rejected video keeps its reason. Up to 3 picks, one per
   channel, most-viewed first. Fewer than 2 usable -> "unavailable" (not an error, not a penalty).
3. confirm_model (model-code products only): a video matched by common name may be a sibling model (the digital
   HHF370 is also a "Honeywell 360"). A different code from the same brand in the transcript rejects it; otherwise an
   LLM may reject it ONLY by citing a segment + verbatim quote showing a feature the listing lacks, and code checks the
   segment and quote exist. Anything else stays "model not confirmed": kept, but its points can't make an issue known.

Run: .venv/bin/python receipts.py          (self-check on phase0/receipts_probe/out, 0 searches, no LLM)
     .venv/bin/python receipts.py --live   (also runs the Groq feature check on the saved heater transcripts)
"""
import contextvars
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import httpx

import lifecycle

MAX_PICKS = 3
MIN_USABLE = 2
MIN_LENGTH_S = 180  # shorts and sub-3-minute clips: too thin to review a product
LLM_MODEL = "openai/gpt-oss-120b"  # same Groq model as the narrator: extraction, merging, mapping
VERIFY_MODEL = LLM_MODEL  # the batched verifier; gpt-oss-20b only if it passes every check (Prompt 5 comparison)
log = logging.getLogger("receipts")


class Abort(Exception):
    """Stops a whole receipts run: never caught by a step's own fallback, so no partial groups are presented."""


class GroqLimit(Abort):
    """Groq's per-day limit, or its per-minute limit still hit after the retries: receipts "try again later"."""


class GroqDown(Abort):
    """Groq unreachable or erroring (network, 5xx): the run stops like at the limit, never degrades to partial groups."""


class NotCached(Abort):
    """Cache-only mode (the pipeline's automatic run) needed a call that isn't cached: receipts run on request."""


CACHE_ONLY = contextvars.ContextVar("receipts_cache_only", default=False)

COMPARISON = re.compile(r"\b(vs\.?|versus|compared|comparison)\b", re.I)
UNBOXING = re.compile(r"\b(unboxing|unbox|first look|hands[- ]on)\b", re.I)
NOT_A_REVIEW = re.compile(r"\b(news|leaks?|rumou?rs?|user guide|how to|tutorial|setup guide)\b", re.I)
DEAL = re.compile(r"\b(deals?|sale|giveaway|discount|coupon|promo code)\b", re.I)
# A genuine review says so, or says how long it was used. Without it, a big unboxing channel ("I've been SPOILED! -
# Sony WH-1000XM5", ShortCircuit, 2.1M views) would outrank real reviews.
REVIEW_WORDS = re.compile(r"\breview(s|ed)?\b|\blong[- ]term\b|\b(months?|years?|weeks?|winters?)\b.*\b(later|on|in|with)\b"
                          r"|\bafter \d+|\bworth (it|buying)\b|\bproblems\b", re.I)
# Words allowed before the product name in its part of the title ("My Nintendo Switch 2 Review"). Anything else
# means the product is a platform label, not the subject: "EA Sports FC 27 Nintendo Switch 2 Review".
LEAD_WORDS = {"my", "the", "a", "an", "honest", "full", "in-depth", "detailed", "official"}
TITLE_PARTS = re.compile(r"\s+[-–—|]\s+|:\s+|\s+\|\s*")
MODEL_CODE = re.compile(r"\b[A-Z]{1,4}-?\d{3,4}[A-Z0-9]*\b", re.I)  # HHF360V, HZ-0360, HHF370B, HHF360VWMC1


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def _code(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _other_codes(text, code):
    """Model codes in text that aren't this one. ponytail: "same brand" = same first letter as our code (Honeywell's
    HHF360V / HHF370 / HZ-0360); keeps a spoken channel name ("sky1007") from reading as a model. Add per-brand code
    prefixes if a brand's codes don't share a letter."""
    return [c for c in MODEL_CODE.findall(text) if c[0].lower() == code[0].lower() and not _same_code(c, code)]


def _same_code(a, b):
    """HHF360 / HHF360V / HHF360VWMC1 are one model (variant suffixes); HHF370 and HZ-0360 aren't."""
    a, b = _code(a), _code(b)
    return a.startswith(b) or b.startswith(a)


def review_target(product, listing_title=None):
    """What a review title must name. product: demo/resolved product (brand, label). listing_title: the listing's own
    title (it carries the model code for products like the heater). None when there's nothing to match on."""
    brand = next(iter((product.get("brand") or "").split()), None)
    if not brand:
        return None
    for name in filter(None, (product.get("label"), listing_title)):
        p = lifecycle.parse_model(name, brand)
        # A real family is short ("WH-1000XM", "Switch", "AirPods Pro"). The heater's title parses as family
        # "HHF360V 360 Degree Surround … &" + 2 (from "2 Heat Settings"): that's a spec, not a generation.
        if p and len(p[0].split()) <= 3:
            return {"kind": "generation", "brand": brand, "family": p[0], "ident": p[1],
                    "name": f"{brand} {p[0]}{p[2]}{p[1]}"}
    codes = [m.group() for m in MODEL_CODE.finditer(listing_title or "")]
    if not codes:
        return None
    code = codes[0]
    # Common name: a standalone number from the title that is also inside the code ("HHF360V ... 360 Degree" -> 360)
    common = next((n for n in re.findall(r"(?<![\w.])(\d{2,4})(?![\w.])", listing_title) if n in code), None)
    return {"kind": "code", "brand": brand, "code": code, "common": common and f"{brand} {common}",
            "name": f"{brand} {code}"}


def _seconds(length):
    try:
        parts = [int(x) for x in (length or "").split(":")]
    except ValueError:
        return 0
    return sum(x * 60 ** i for i, x in enumerate(reversed(parts)))


def _names_model(target, title):
    """(match object for the product mention, None) or (None, reason it doesn't name this product)."""
    brand = rf"\b{re.escape(target['brand'])}(?:['’]s)?\W+"
    if not re.search(rf"\b{re.escape(target['brand'])}", title, re.I):
        return None, f"doesn't name {target['brand']}"
    if target["kind"] == "generation":
        ids = lifecycle._ids(target["family"], title)
        if ids and ids != {target["ident"]}:
            other = max(ids - {target["ident"]}) if ids - {target["ident"]} else target["ident"]
            return None, f"other model ({target['family']}{other})"
        m = re.search(brand + r"(?:\w+\W+){0,2}?" + lifecycle._family_re(target["family"]).pattern, title, re.I)
        return (m, None) if m else (None, f"doesn't name the {target['name']} (other product)")
    others = _other_codes(title, target["code"])
    if others:
        return None, f"other model ({others[0]})"
    m = re.search(brand + rf"(?:\w+\W+){{0,2}}?{re.escape(target['code'])}", title, re.I) or \
        (target["common"] and re.search(brand + re.escape(target["common"].split()[-1]) + r"(?!\d)", title, re.I))
    return (m, None) if m else (None, f"doesn't name the {target['common'] or target['name']} (other product)")


def _is_subject(title, m):
    """The product must lead its part of the title: "My Nintendo Switch 2 Review" yes, "EA Sports FC 27 Nintendo
    Switch 2 Review" / "Minecraft Dungeons 2 on Nintendo Switch 2" / "10 NEW Nintendo Switch 2 eShop Games" no."""
    start = max([0] + [p.end() for p in TITLE_PARTS.finditer(title) if p.end() <= m.start()])
    return all(w.lower() in LEAD_WORDS for w in title[start:m.start()].split())


def _reject_reason(target, v, maker_channel):
    title = v.get("title") or ""
    if maker_channel.search(v["channel"]["name"]):
        return "maker's own channel"
    if _seconds(v.get("length")) < MIN_LENGTH_S:
        return f"short ({v.get('length') or 'no length'})"
    if COMPARISON.search(title):
        return "comparison"
    if UNBOXING.search(title) and not re.search(r"\breview", title, re.I):
        return "unboxing / first look"
    if NOT_A_REVIEW.search(title):
        return "not a review (news / how-to)"
    if DEAL.search(title):
        return "deal / sale / giveaway"
    m, why = _names_model(target, title)
    if not m:
        return why
    if not _is_subject(title, m):
        return "product isn't the subject of the title"
    if not REVIEW_WORDS.search(title):
        return "not titled as a review"
    return None


def select_review_videos(product, youtube_results, listing_title=None):
    """youtube_results: the engine=youtube response. Returns {status, target, picked, rejected}; every video in
    video_results appears in exactly one of picked / rejected, with its reason. status: found | unavailable."""
    target = review_target(product, listing_title)
    videos = (youtube_results or {}).get("video_results") or []
    slim = lambda v, **x: {"video_id": v.get("video_id"), "title": v.get("title"), "channel": v["channel"]["name"],
                           "views": v.get("views") or 0, "published": v.get("published_date"),
                           "length": v.get("length"), "link": v.get("link"), **x}
    if not target:
        return {"status": "unavailable", "why": "no model to match titles on", "target": None, "picked": [],
                "rejected": [slim(v, why="no model to match") for v in videos]}
    maker_channel = re.compile(rf"^{re.escape(target['brand'])}\b", re.I)
    usable, rejected = [], []
    for v in videos:
        why = _reject_reason(target, v, maker_channel)
        (rejected.append(slim(v, why=why)) if why else usable.append(v))
    # ponytail: "published after release" isn't checked: YouTube dates are relative ("4y ago") and a title that names
    # the exact model can't predate it (leaks are rejected as news). Most views first, then longer runtime.
    usable.sort(key=lambda v: (v.get("views") or 0, _seconds(v.get("length"))), reverse=True)
    picked, channels = [], set()
    for v in usable:
        ch = v["channel"]["name"]
        if ch in channels:
            rejected.append(slim(v, why=f"same channel as a better-ranked video ({ch})"))
            continue
        channels.add(ch)
        if len(picked) < MAX_PICKS:
            picked.append(slim(v, model="confirmed" if target["kind"] == "generation" or
                               re.search(re.escape(target["code"]), v["title"], re.I) else "not confirmed"))
        else:
            rejected.append(slim(v, why="spare: ranked below the picks"))
    # model "not confirmed" (name matched, code not seen) still counts toward "usable"; confirm_model decides later
    status = "found" if len(picked) >= MIN_USABLE else "unavailable"
    return {"status": status, "target": target, "picked": picked, "rejected": rejected,
            **({"why": f"only {len(picked)} usable review video(s)"} if status == "unavailable" else {})}


FEATURE_PROMPT = """You check whether a product review video is about the same model as a store listing.
The listing's features are the complete description of the product being sold. The transcript is numbered by segment.
Reply "different_model" ONLY if a segment shows the reviewed product HAS a feature the listing's product clearly does
NOT have (e.g. a digital display or buttons where the listing says manual). Controls are features: if the listing says
its controls are manual, a reviewed unit set with digital controls (up/down or +/- buttons, a digital display or
readout) has a feature the listing lacks. A feature the video's product LACKS is never evidence. Missing information is
never evidence. If unsure, reply "no_conflict".
Return JSON: {"verdict": "different_model" | "no_conflict", "feature": "<the feature, or empty>",
"segment": <segment number, or null>, "quote": "<verbatim words copied from that segment, max 15 words, or empty>"}"""


def _groq(system, user, model=LLM_MODEL):
    """One Groq call. Free tier: 8,000 tokens/min and 200,000 tokens/day per model. A per-minute 429 waits out
    retry-after (up to 30 s, 3 tries); a per-day 429, or a per-minute one that outlasts the retries, raises GroqLimit."""
    for attempt in range(4):
        try:
            r = httpx.post("https://api.groq.com/openai/v1/chat/completions",
                           headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
                           json={"model": model, "temperature": 0, "reasoning_effort": "low",
                                 "response_format": {"type": "json_object"},
                                 "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                           timeout=30)
        except (httpx.HTTPError, OSError) as e:
            raise GroqDown(f"Groq {model}: {type(e).__name__}") from e
        if r.status_code >= 500:
            raise GroqDown(f"Groq {model}: HTTP {r.status_code}")
        if r.status_code != 429:
            break
        wait = float(r.headers.get("retry-after") or 5)
        if "per day" in r.text or wait > 30 or attempt == 3:
            raise GroqLimit(f"Groq {model}: {(r.json().get('error') or {}).get('message', '')[:160]}")
        time.sleep(wait)
    r.raise_for_status()
    tokens = (r.json().get("usage") or {}).get("total_tokens") or 0
    GROQ_TOKENS[0] += tokens
    log.info("groq %s: %d tokens", model, tokens)
    return json.loads(r.json()["choices"][0]["message"]["content"])


def confirm_model(target, video, transcript, listing_features, llm=_groq):
    """Model-code products only. transcript: youtube_video_transcript response. Returns
    {model: confirmed | not confirmed | rejected, why, evidence?}. Never raises: no transcript or an LLM failure
    leaves the video "not confirmed"."""
    if target["kind"] != "code":
        return {"model": "confirmed", "why": "title names brand + family + identifier"}
    segs = (transcript or {}).get("transcript") or []
    if not segs:
        return {"model": "not confirmed", "why": "no transcript"}
    text = " ".join(s["snippet"] for s in segs)
    others = _other_codes(text, target["code"])
    if others:
        return {"model": "rejected", "why": f"transcript names another model ({others[0]})"}
    if any(_same_code(c, target["code"]) for c in MODEL_CODE.findall(text)) or re.search(re.escape(target["code"]), video["title"], re.I):
        return {"model": "confirmed", "why": f"names {target['code']}"}
    numbered = "\n".join(f"[{i}] {s['snippet']}" for i, s in enumerate(segs))
    llm = _with_cache(llm, "groq_receipts_confirm", video_id=video.get("video_id"))
    try:
        out = llm(FEATURE_PROMPT, f"LISTING FEATURES:\n- " + "\n- ".join(listing_features) + f"\n\nTRANSCRIPT:\n{numbered}")
    except Abort:
        raise
    except Exception as e:  # noqa: BLE001 — an LLM failure never rejects a video
        return {"model": "not confirmed", "why": f"feature check failed: {type(e).__name__}"}
    if out.get("verdict") != "different_model":
        return {"model": "not confirmed", "why": "no conflicting feature found; code not named"}
    i, quote = out.get("segment"), out.get("quote") or ""
    # Grounding: the cited segment must exist and contain the quote (a neighbour is allowed: sentences span segments)
    if not isinstance(i, int) or not 0 <= i < len(segs) or not quote.strip():
        return {"model": "not confirmed", "why": "rejection cited no valid segment", "llm": out}
    around = _norm(" ".join(s["snippet"] for s in segs[max(i - 1, 0):i + 2]))
    if _norm(quote) not in around:
        return {"model": "not confirmed", "why": "rejection quote not found in the cited segment", "llm": out}
    return {"model": "rejected", "why": f"has a feature the listing lacks: {out.get('feature')}",
            "evidence": {"segment": i, "start_ms": segs[i]["start_ms"], "start_time_text": segs[i]["start_time_text"],
                         "quote": quote}}


POINTS_PROMPT = """You extract what a product review video says about ONE product: {name}.
The transcript is numbered by segment: [n] text.
Return JSON {{"points": [{{"kind": "issue" | "praise", "label": "<2-5 word label, e.g. 'headband creaks',
'ANC weaker on wind', 'joy-con drift', 'battery ~30 hours'>", "thing": "<the concrete part, feature, behavior or
measurement it is about>", "segment": <the segment number>, "anchor": "<4-10 CONSECUTIVE words copied from that
segment>"}}]}}
Rules:
- Only the reviewer's own verdict on {name} itself. When they compare it with another product (an older model, a
  rival, another unit), never take a point about the other product.
- Skip sponsor and ad reads, and games/apps/content.
- Every point names something concrete: a part, a feature, a behavior, a measurement. Never "it's great", "not perfect",
  "some people might not like it", "worth the money" alone.
- The anchor is one unbroken run of 4-10 words from that one segment, copied exactly as transcribed (keep its errors).
  Never "...", never join separate pieces.
- One point per distinct issue/praise; at most 15 points. Issues first."""

MERGE_PROMPT = """These points were extracted from different review videos of the same product. Group points that
describe the SAME specific issue or the SAME specific praise (e.g. "doesn't fold" and "case too big for travel, no
folding" are one; "battery life short" and "charging slow" are not). Never put an issue and a praise in one group.
Give each group a short shared label (2-5 words). Every point number appears in exactly one group.
Return JSON {"groups": [{"label": "<label>", "members": [<point numbers>]}]}"""

VERIFY_PROMPT = """You check one claim about {name} against its evidence items. Claim ({kind}): "{label}"
Each item is a review-video excerpt (segments numbered [n]) or a forum thread (title + search snippet). For EACH item,
answer "yes" only if ALL of these hold:
1. It states this claim itself, directly. An inference doesn't count ("falls short of the OLED model" does not state
   "has an LCD"), and neither does a narrower, broader or merely related claim.
2. It is about {name} itself, not a product it is compared with, a sponsor's product, a game or an app.
3. Polarity: for an issue, it is presented as a drawback, a complaint, or a risk the reviewer raises about {name}
   (a raised risk counts: code labels it a concern, never a reported problem); for a praise, as something good. A
   neutral spec or the opposite is "no".
4. A forum thread must describe the same symptom, not a related one ("battery drains fast" is not "charging is slow";
   a fault in the device is not a comfort complaint about using it).
Return JSON {{"answers": [{{"point_id": "<the item's id>", "supports": "yes" | "no", "cited_segment": <the segment
number that states it, for a video item; null for a thread>, "same_symptom": "<for a thread: yes | no, is it the very
same symptom as the claim (rule 4); empty for a video item>", "words": "<for a thread: 2-8 words copied from its title
or snippet that state it; empty for a video item>"}}]}} with exactly one answer per item."""

QUOTE_MAX_WORDS = 15
ANCHOR_WORDS = (4, 15)  # = the quote cap. POINTS_PROMPT still asks for 4-10, so cached extractions stay valid
CHUNK_CHARS = 10_000  # ~2.5k tokens: one chunk + prompt + reply stays under Groq's 8k tokens/min
# Calibrated on the 9 saved transcripts (Prompt 3 run 1): a verified quote vs far-away segments maxes at 0.82, cut-out
# middles score 0.79-0.88, "..." elisions 0.90-0.94, genuine ASR slips ("amc"/"anc", "they're"/"these are") 0.96-0.99.
FUZZY_MIN = 0.95
STOP = set("a an the of on in at to for with and or is are was it its it's this that these those very really pretty "
           "too so not no just bit little more less much quite".split())
VAGUE = set("great good bad nice amazing awesome solid fine decent perfect imperfect overall product device thing "
            "things stuff experience issue issues problem problems con cons pro pros quality worth love like hate "
            "some people might may could feel feels okay ok best worst better worse".split())
# Sponsor reads: a chapter named as one, or a trigger phrase up to its end marker (vanity URL / "link below"), the next
# chapter, or 90 s, whichever comes first. LTT's dbrand read ends at "...very small.com", so its 1:24 drift line stays.
SPONSOR_CHAPTER = re.compile(r"sponsor|\bad break\b|\badvert", re.I)
SPONSOR_START = re.compile(r"\bsponsor(ed|s|ing)?\b|\buse (my |our |the )?code\b|\bpromo code\b", re.I)
SPONSOR_END = re.compile(r"\blink (will be |is )?(below|in the description)|\bdescription below\b|\w\.com\b", re.I)
SPONSOR_MAX_MS = 90_000
GROQ_TOKENS = [0]  # running total, for latency/budget reports


def sponsor_segments(transcript):
    """Indexes of transcript segments inside a sponsor read."""
    segs = (transcript or {}).get("transcript") or []
    chapters = sorted((transcript or {}).get("chapters") or [], key=lambda c: c["start_ms"])
    starts = [c["start_ms"] for c in chapters]
    out = set()
    for n, c in enumerate(chapters):
        if SPONSOR_CHAPTER.search(c["chapter"]):
            end = starts[n + 1] if n + 1 < len(starts) else float("inf")
            out |= {i for i, s in enumerate(segs) if c["start_ms"] <= s["start_ms"] < end}
    for i, s in enumerate(segs):
        if i in out or not SPONSOR_START.search(s["snippet"]):
            continue
        stop = min([s["start_ms"] + SPONSOR_MAX_MS] + [t for t in starts if t > s["start_ms"]])
        for j in range(i, len(segs)):
            if segs[j]["start_ms"] >= stop:
                break
            out.add(j)
            if SPONSOR_END.search(segs[j]["snippet"]):
                break
    return out


def _clean_segs(transcript):
    """Segments with sponsor reads blanked: the LLM never sees them and no anchor can match there."""
    ads = sponsor_segments(transcript)
    return [{**s, "snippet": ""} if i in ads else s for i, s in enumerate((transcript or {}).get("transcript") or [])]


def _with_cache(llm, engine, sanitize=None, model=LLM_MODEL, **ident):
    """The real Groq call goes through serpapi_cache like a SerpApi response (same TTL; demo_fixtures pins what the
    demos read), so a product checked once costs no Groq calls. Key: ident + a hash of the exact input, so a prompt or
    transcript change asks again. Stubs (tests) bypass it. LLM_LOG records every call for reports.
    sanitize: applied to a fresh reply before it is stored or returned (forum calls use it so snippet words never
    leave the call). CACHE_ONLY set: a miss raises NotCached instead of calling Groq."""
    sanitize = sanitize or (lambda r: r)
    if llm is not _groq:
        return lambda system, user: sanitize(llm(system, user))

    def call(system, user):
        key = {**ident, "input": hashlib.sha256(f"{model}\n{system}\n{user}".encode()).hexdigest()}
        t0, tok0 = time.monotonic(), GROQ_TOKENS[0]
        try:
            import serpapi
            hit = serpapi.cached(engine, key)
        except Exception:  # noqa: BLE001 — no cache is slower, not broken
            serpapi, hit = None, None
        if not hit and CACHE_ONLY.get():
            raise NotCached(engine)
        reply = hit["reply"] if hit else sanitize(_groq(system, user, model))
        if serpapi and not hit:
            try:
                serpapi.db.table("serpapi_cache").upsert(
                    {"engine": engine, "query_params_hash": serpapi._hash(engine, key), "response_json": {"reply": reply},
                     "fetched_at": datetime.now(timezone.utc).isoformat()},
                    on_conflict="engine,query_params_hash").execute()
            except Exception:  # noqa: BLE001
                pass
        LLM_LOG.append({"engine": engine, "model": model, **ident, "cached": bool(hit), "s": time.monotonic() - t0,
                        "tokens": GROQ_TOKENS[0] - tok0, "reply": reply})
        log.info("%s %s: %s, %d tokens", engine, model, "cached" if hit else "live", GROQ_TOKENS[0] - tok0)
        return reply
    return call


LLM_LOG = []


def _words(s):
    return _norm(s).split()


def _tokens(segs, ix):
    """Normalized words of segments ix, each as (word, segment index, original token index)."""
    return [(w, i, j) for i in ix for j, tok in enumerate(segs[i]["snippet"].split()) for w in _words(tok)]


def _places(anchor, toks):
    """Distinct places in toks where the anchor matches (ratio >= FUZZY_MIN), as (ratio, start, end), best first.
    Overlapping windows are one place."""
    from difflib import SequenceMatcher
    a = " ".join(_words(anchor))
    n = len(a.split())
    hits = []
    for k in {max(1, n + d) for d in (-1, 0, 1)}:  # ASR splits/joins words ("usbc" / "usb c")
        for i in range(len(toks) - k + 1):
            m = SequenceMatcher(None, a, " ".join(t[0] for t in toks[i:i + k]))
            if m.quick_ratio() >= FUZZY_MIN and (r := m.ratio()) >= FUZZY_MIN:
                hits.append((r, i, i + k))
    places = []
    for h in sorted(hits, reverse=True):
        if all(h[2] <= p[1] or h[1] >= p[2] for p in places):
            places.append(h)
    return places


CONTRAST = re.compile(r"^(but|however|though|although|yet|while)$|[.!?]$", re.I)


def _quote(segs, toks, start, end, from_anchor=False):
    """The transcript's own words (original casing/punctuation) around the matched anchor, within the segment(s) it
    sits in, at most QUOTE_MAX_WORDS. from_anchor: start exactly at the anchor (extend right only)."""
    lo_seg, hi_seg = toks[start][1], toks[end - 1][1]
    orig = [(i, j, tok) for i in range(lo_seg, hi_seg + 1) for j, tok in enumerate(segs[i]["snippet"].split())]
    pos = {(i, j): n for n, (i, j, _) in enumerate(orig)}
    lo, hi = pos[toks[start][1:]], pos[toks[end - 1][1:]] + 1
    hi = min(hi, lo + QUOTE_MAX_WORDS)
    while hi - lo < QUOTE_MAX_WORDS and ((lo > 0 and not from_anchor) or hi < len(orig)):
        lo -= lo > 0 and not from_anchor
        hi += hi - lo < QUOTE_MAX_WORDS and hi < len(orig)
    return " ".join(t for _, _, t in orig[lo:hi])


def _display_quote(segs, toks, start, end):
    """The quote as shown: if the words before the anchor end a sentence or turn ("…regression in portability here.
    It is still slim…"), they may say the opposite of the point, so the shown quote starts at the anchor. The merge
    still sees the plain quote, so cached merges stay valid."""
    q, a = _quote(segs, toks, start, end), _quote(segs, toks, start, end, from_anchor=True)
    before = q[: max(0, len(q) - len(a))] if q.endswith(a) else q.split(a.split()[0], 1)[0]
    return a if any(CONTRAST.search(w) for w in before.split()) else q


def _is_vague(label, thing):
    """No content word left after dropping stopwords and vague words, in either the label or the named thing."""
    concrete = lambda s: [w for w in _words(s) if w not in STOP and w not in VAGUE and not w.isdigit()]
    return not concrete(label) or not concrete(thing)


def locate_point(p, segs):
    """(clean point, None) or (None, reason). Code-only: the anchor must be in the cited segment or the next one; if
    not, anywhere in the transcript but at exactly one place (re-anchored). Quote and timestamp come from the
    transcript, never the LLM. segs: sponsor-blanked segments."""
    if p.get("kind") not in ("issue", "praise"):
        return None, "bad kind"
    label, thing, anchor = ((p.get(k) or "").strip() for k in ("label", "thing", "anchor"))
    i = p.get("segment")
    if not isinstance(i, int) or not 0 <= i < len(segs):
        return None, "cited segment invalid"
    if "..." in anchor or "…" in anchor:
        return None, "elided anchor"
    if not ANCHOR_WORDS[0] <= len(anchor.split()) <= ANCHOR_WORDS[1]:  # as written: "Joy-Con" is one word
        return None, f"anchor not {ANCHOR_WORDS[0]}-{ANCHOR_WORDS[1]} words"
    if _is_vague(label, thing):
        return None, "vague"
    toks = _tokens(segs, [i, i + 1] if i + 1 < len(segs) else [i])
    near = _places(anchor, toks)
    how = "cited"
    if not near:
        toks = _tokens(segs, range(len(segs)))
        near = _places(anchor, toks)
        if len(near) != 1:
            return None, "anchor not in transcript" if not near else f"anchor ambiguous ({len(near)} places)"
        how = "re-anchored"
    r, start, end = near[0]
    seg = toks[start][1]
    cited = " ".join(segs[i]["snippet"] for i in range(seg, toks[end - 1][1] + 1))
    return {"kind": p["kind"], "label": label.lower(), "quote": _quote(segs, toks, start, end),
            "quote_display": _display_quote(segs, toks, start, end),
            "segment_start_s": segs[seg]["start_ms"] // 1000, "segment": seg, "match": round(r, 3), "how": how,
            "hedged": p["kind"] == "issue" and is_hedged(cited)}, None


def extract_points(video, transcript, name=None, llm=_groq):
    """video: a select_review_videos pick (video_id, title). transcript: youtube_video_transcript response. name: the
    product's clean name. Sponsor reads are removed before the LLM sees the transcript. Returns {video_id, points,
    dropped: {reason: n}, dropped_examples, raw_count, error?}. Never raises: no transcript or an LLM failure gives no
    points and an error. Callers drop videos confirm_model rejects first (points_for_video does both)."""
    segs = _clean_segs(transcript)
    out = {"video_id": video["video_id"], "points": [], "dropped": {}, "dropped_examples": []}
    if not any(s["snippet"] for s in segs):
        return {**out, "error": "no transcript"}
    chunks, size = [[]], 0  # by whole segments; numbers stay global so citations index the full transcript
    for i, s in enumerate(segs):
        if not s["snippet"]:
            continue
        if size + len(s["snippet"]) > CHUNK_CHARS and chunks[-1]:
            chunks.append([])
            size = 0
        chunks[-1].append(f"[{i}] {s['snippet']}")
        size += len(s["snippet"])
    llm = _with_cache(llm, "groq_receipts_points", video_id=video["video_id"])
    try:
        raw = [p for c in chunks
               for p in llm(POINTS_PROMPT.format(name=name or video.get("title")), "\n".join(c)).get("points") or []]
    except Abort:
        raise
    except Exception as e:  # noqa: BLE001 — no points beats a crash
        return {**out, "error": f"extraction failed: {type(e).__name__}"}
    seen = set()
    for p in raw:
        got, why = locate_point(p, segs) if isinstance(p, dict) else (None, "bad kind")
        if got and (got["kind"], got["label"]) in seen:
            got, why = None, "duplicate label"
        if not got:
            out["dropped"][why] = out["dropped"].get(why, 0) + 1
            out["dropped_examples"].append({"why": why, **(p if isinstance(p, dict) else {"raw": p})})
            continue
        seen.add((got["kind"], got["label"]))
        out["points"].append({**got, "video_id": video["video_id"]})
    out["raw_count"] = len(raw)
    return out


def points_for_video(target, video, transcript, listing_features=None, llm=_groq):
    """confirm_model, then extract_points. A video confirm_model rejects (a sibling model) contributes no points."""
    m = confirm_model(target, video, transcript, listing_features or [], llm=llm)
    src = {"channel": video.get("channel"), "link": video.get("link"), "model": m}
    if m["model"] == "rejected":
        return {"video_id": video["video_id"], "points": [], "dropped": {}, "dropped_examples": [], "skipped": m, **src}
    r = extract_points(video, transcript, target_name(target), llm=llm)
    return {**r, **src, "points": [{**p, "channel": video.get("channel"), "model": m["model"]} for p in r["points"]]}


def target_name(target):
    return target["name"] if target["kind"] == "generation" else f"{target['common'] or target['brand']} ({target['code']})"


def merge_points(points, llm=_groq):
    """points: every video's points for one product. Returns [{kind, label, points: [original points]}]. The LLM only
    proposes groups; code keeps each point once, never mixes issue and praise, and keeps original quotes. An LLM
    failure leaves every point its own group."""
    if not points:
        return []
    numbered = "\n".join(f"[{i}] ({p['kind']}) {p['label']} — \"{p['quote']}\"" for i, p in enumerate(points))
    llm = _with_cache(llm, "groq_receipts_merge", videos=sorted({p.get("video_id") or "" for p in points}))
    try:
        groups = llm(MERGE_PROMPT, numbered).get("groups") or []
    except Abort:
        raise
    except Exception:  # noqa: BLE001
        groups = []
    used, merged = set(), []
    for g in groups:
        members = [i for i in dict.fromkeys(g.get("members") or []) if isinstance(i, int) and 0 <= i < len(points) and i not in used]
        for kind in ("issue", "praise"):  # a mixed group splits by kind
            ms = [i for i in members if points[i]["kind"] == kind]
            if not ms:
                continue
            label = (g.get("label") or "").strip().lower()
            if not label or len(label.split()) > 6 or len({points[i]["kind"] for i in members}) > 1:
                label = points[ms[0]]["label"]
            used.update(ms)
            merged.append({"kind": kind, "label": label, "points": [points[i] for i in ms]})
    merged += [{"kind": p["kind"], "label": p["label"], "points": [p]} for i, p in enumerate(points) if i not in used]
    return merged


def _independent(g):
    """Independent sources behind a group: each model-confirmed video (one per channel, by selection) and each forum
    thread. A "not confirmed" video (name matched, model code never seen) doesn't count; marketplace never does."""
    return ({("video", p["video_id"]) for p in g["points"] if p.get("model", "confirmed") == "confirmed"}
            | {("forum", f["link"]) for f in g.get("forums") or []})


def _is_known(g, min_sources=2):
    """2+ independent sources, at least one a video: different channels, or a video plus a forum thread."""
    ind = _independent(g)
    return len(ind) >= min_sources and any(k == "video" for k, _ in ind)


def verify_group(name, g, transcripts, llm=_groq, model=None):
    """One LLM call checks every source of a group: each video point (its segment ±2, sponsor reads blanked) and each
    forum thread (title + snippet). Returns {point_id: (ok, why)} with ids "v0".. for g["points"] and "t0".. for
    g["forums"]. Code checks every id is answered exactly once; a missing, duplicated or malformed answer fails that
    id. A video "yes" must cite a segment in its own window; a thread "yes" must quote 2+ words found in its title or
    snippet (checked inside the call, then dropped: no snippet text leaves it)."""
    model = model or VERIFY_MODEL
    items, windows, threads = [], {}, {}
    for i, p in enumerate(g["points"]):
        segs = _clean_segs(transcripts.get(p["video_id"]))
        win = [j for j in range(p["segment"] - 2, p["segment"] + 3) if 0 <= j < len(segs) and segs[j]["snippet"]]
        windows[f"v{i}"] = win
        items.append(f"### v{i} (review video)\n" + "\n".join(f"[{j}] {segs[j]['snippet']}" for j in win))
    for i, f in enumerate(g.get("forums") or []):
        t = f["_thread"]
        threads[f"t{i}"] = t
        items.append(f"### t{i} (forum thread)\ntitle: {t.get('title')}\nsnippet: {t.get('snippet') or ''}")
    ids = list(windows) + list(threads)

    def ground(reply):  # thread words checked here and dropped, with any free-text reasoning
        out = []
        for a in reply.get("answers") or []:
            if not isinstance(a, dict):
                continue
            pid = a.get("point_id")
            out.append({"point_id": pid, "supports": a.get("supports"), "cited_segment": a.get("cited_segment"),
                        "same_symptom": a.get("same_symptom"),
                        "grounded": pid in threads and _grounded(a.get("words") or "", _mapping_text(threads[pid]), 2)})
        return {"answers": out}
    llm = _with_cache(llm, "groq_receipts_verify_group", sanitize=ground, model=model, label=g["label"], ids=ids)
    empty = [i for i in windows if not windows[i]]
    try:
        answers = llm(VERIFY_PROMPT.format(name=name, kind=g["kind"], label=g["label"]), "\n\n".join(items))["answers"]
    except Abort:
        raise
    except Exception as e:  # noqa: BLE001 — a failed check never passes a point
        detail = f" {e.response.status_code}: {e.response.text[:120]}" if isinstance(e, httpx.HTTPStatusError) else ""
        log.warning("verifier failed for %r: %s%s", g["label"], type(e).__name__, detail)
        return {i: (False, f"verifier failed: {type(e).__name__}{detail}") for i in ids}
    seen = {}
    for a in answers:
        seen.setdefault(a.get("point_id"), []).append(a)
    res = {}
    for i in ids:
        got = seen.get(i) or []
        if i in empty:
            res[i] = (False, "no transcript to verify against")
        elif len(got) != 1:
            res[i] = (False, "verifier answered it twice" if got else "verifier didn't answer it")
        elif got[0]["supports"] != "yes":
            res[i] = (False, "verifier: no" if got[0]["supports"] == "no" else "verifier answer malformed")
        elif i in windows and got[0]["cited_segment"] not in windows[i]:
            res[i] = (False, "verifier cited no segment in the window")
        elif i in threads and got[0].get("same_symptom") != "yes":  # explicit: a related symptom never joins a group
            res[i] = (False, "verifier: different symptom")
        elif i in threads and not got[0]["grounded"]:
            res[i] = (False, "verifier quoted words not in the thread")
        else:
            res[i] = (True, "verifier: yes")
    return res


def _forum_known(g):
    """Forum-only: no video raised it, but 3+ separate (verified) threads report it."""
    return not g["points"] and len(g.get("forums") or []) >= FORUM_ONLY_MIN_THREADS


def verify_groups(groups, transcripts, name, llm=_groq, min_sources=2, model=None):
    """Only groups that would be known (_is_known, or forum-only with 3+ threads) get the verifier, one call per group.
    Failures move to left_group (with why); known / forum_known are recomputed after it.
    transcripts: {video_id: youtube_video_transcript response}."""
    for g in groups:
        g["known"], g["forum_known"], g["left_group"] = False, False, []
        if not (_is_known(g, min_sources) or _forum_known(g)):
            continue
        res = verify_group(name, g, transcripts, llm=llm, model=model)
        keep = [p for i, p in enumerate(g["points"]) if res[f"v{i}"][0]]
        fkeep = [f for i, f in enumerate(g.get("forums") or []) if res[f"t{i}"][0]]
        g["left_group"] = [{**p, "why": res[f"v{i}"][1]} for i, p in enumerate(g["points"]) if not res[f"v{i}"][0]] + \
                          [{"type": "forum", **_forum_src(f), "why": res[f"t{i}"][1]}
                           for i, f in enumerate(g.get("forums") or []) if not res[f"t{i}"][0]]
        g["points"], g["forums"] = keep, fkeep
        g["known"], g["forum_known"] = _is_known(g, min_sources), _forum_known(g)
    return groups


FORUM_PROMPT = """Review videos of {name} raised these points (number, kind, label):
{labels}
Below are forum threads: title, the words of its URL, and a search snippet. For each thread whose title or snippet
names a SPECIFIC problem with {name} itself, map it to the number of the video point with the same problem, or to a new
short issue label. Same problem means the same symptom, not a related one ("battery drains fast" is not "charging is
slow"; a fault in the device is not a comfort complaint about using it): when only related, use a new label. Skip
threads that only ask for reviews or "common issues" without naming one, and threads about a game, an app, a computer
or another device.
Return JSON {{"maps": [{{"thread": <thread number>, "label": <video point number> | "new: <2-5 word issue label>",
"words": "<2-8 words copied from that thread's title, URL words or snippet that name the problem>"}}]}}"""

MARKET_PROMPT = """Review videos of {name} raised these points (number, kind, label):
{labels}
Below are marketplace review insights (title, sentiment, summary). Map an insight to a video point ONLY if it clearly
describes the same thing (e.g. "Battery life" with "short battery life"). Most insights match nothing: skip them.
Return JSON {{"maps": [{{"insight": <insight number>, "label": <video point number>,
"words": "<1-8 words copied from that insight's title or summary that show the match>"}}]}}"""

# Display: sentence case with known acronyms kept. Something failing (a defect) vs a design choice or price point (a
# common criticism): the UI's chip style and the narrator's wording both come from issue_type.
ACRONYMS = {"usb-c": "USB-C", "usbc": "USB-C", "usb-a": "USB-A", "usb": "USB", "anc": "ANC", "lcd": "LCD", "oled": "OLED",
            "joy-con": "Joy-Con", "joy-cons": "Joy-Cons", "joycon": "Joy-Con", "joycons": "Joy-Cons", "vrr": "VRR",
            "hdmi": "HDMI", "hdr": "HDR", "hdr10": "HDR10", "4k": "4K", "4k60": "4K60", "hz": "Hz", "ui": "UI",
            "ldac": "LDAC", "nfc": "NFC", "aptx": "aptX", "sr/sl": "SR/SL", "microsd": "microSD", "fps": "fps"}
LABEL_TAIL = re.compile(r"\s+(praise|issues?|drawbacks?|concerns?|problems?|complaints?)$", re.I)
FAILING = re.compile(r"\b(break|broke|broken|crack|snap|fail|drift|stops?|dead|shut|disconnect|drops?|creak|rattl|leak|"
                     r"melt|burn|overheat|won'?t|freez|crash|peel|loose|stuck|flicker|buzz|hiss)", re.I)


def display_label(label):
    """"no usb‑c audio" -> "No USB-C audio", "lcd vs oled drawbacks" -> "LCD vs OLED", "joy‑con drift issues" ->
    "Joy-Con drift". Trailing filler ("praise", "issues") dropped."""
    s = re.sub(r"[\u2010\u2011\u2012\u2013]", "-", label or "").replace("\u202f", " ").strip().lower()
    s = LABEL_TAIL.sub("", s)
    s = re.sub(r"(\d)\s*hz\b", r"\1 hz", s)
    words = [ACRONYMS.get(w, w) for w in s.split()]
    if words and words[0] == words[0].lower():
        words[0] = words[0][:1].upper() + words[0][1:]
    return " ".join(words)


def issue_type(kind, label):
    return "praise" if kind == "praise" else "problem" if FAILING.search(label or "") else "criticism"


# Speculation vs a reported issue, decided in code from the cited transcript text: a hedge ("will these also start…",
# "susceptible to", "same tech as") with nothing reported ("mine", "I've had", "started to") is a risk, not a problem.
HEDGE = re.compile(r"\b(will (these|they|it|this|that)|may|might|could|susceptible|only time will tell|remains to be seen|"
                   r"same tech(nology)?)\b", re.I)
REPORT = re.compile(r"\b(mine|my (unit|console|pair|one|controllers?|joy-?cons?|headphones)|i'?ve had|i have had|"
                    r"started to|developed|happened to me)\b", re.I)


def is_hedged(text):
    return bool(HEDGE.search(text or "")) and not REPORT.search(text or "")


def group_type(g):
    """problem / criticism / praise from the label; a "problem" that fewer than 2 sources REPORT (hedged video points
    don't report; a forum thread does) is a "concern": a risk reviewers raise. Concerns can be known like issues."""
    t = issue_type(g["kind"], g["label"])
    if t != "problem":
        return t
    reporters = {p["video_id"] for p in g["points"] if not p.get("hedged")} | {f["link"] for f in g.get("forums") or []}
    return "problem" if len(reporters) >= 2 else "concern"


# A thread whose title asks a general question names no specific symptom: it may guide mapping, never be a source.
ROUNDUP = re.compile(r"\b(common|known|any|other|some)\b[^?]{0,40}\b(issues?|problems?|defects?|flaws?|bugs?)\b"
                     r"|\b(problems?|issues?) with\b[^?]*\?\s*$", re.I)
FORUM_ONLY_MIN_THREADS = 3  # forum-only issue is "reported by owners in forums" at 3+ separate threads (prefer 2+ sites)
FORUM_FIELDS = ("title", "link", "source", "date")  # all a forum source ever shows or stores: never the snippet


def _thread_text(t):
    """A thread's title plus its URL's words: Google cuts long titles ("...hardware flaws plaguing the ..."), the URL
    slug often has the rest ("...-Nintendo-Switch-2-besides-stick-drift")."""
    slug = re.sub(r"[-_]+", " ", (t.get("link") or "").rstrip("/").split("/")[-1].split("?")[0])
    return f"{t.get('title') or ''} — {slug}"


def _mapping_text(t):
    """Title + URL words + snippet: what a forum mapping may quote. Internal only (snippets are post-body excerpts)."""
    return f"{_thread_text(t)} — {t.get('snippet') or ''}"


def forum_names_model(target, thread):
    """Forum titles often drop the brand ("WH-1000XM5 keeps shutting down", "common issues with switch 2"): the family +
    identifier is enough, and so is its short form ("XM5 Headphone Design Flaw"). Another generation never is.
    Model-code products: the same rule as video titles (brand + code or common name). Title + URL only."""
    text = _thread_text(thread)
    if target["kind"] == "code":
        return bool(_names_model(target, text)[0])
    short = re.search(r"[A-Za-z]{2,}$", target["family"])
    ids = lifecycle._ids(target["family"], text) | \
        ({int(n) for n in re.findall(rf"\b{short.group()}[\s-]?(\d+)\b", text, re.I)} if short else set())
    return ids == {target["ident"]}


def _grounded(words, text, min_words):
    w = _words(words)
    return len(w) >= min_words and f" {' '.join(w)} " in f" {' '.join(_words(text))} "


def _forum_src(t):
    return {k: t.get(k) for k in FORUM_FIELDS}


def map_forums(groups, threads, name, llm=_groq):
    """Attach forum threads to groups (g["forums"]); a thread naming a problem no video raised starts a forum-only
    group. Every mapping cites a thread and 2+ words code finds in its title, URL or snippet. The words are checked
    inside the call and dropped (sanitize), so no snippet text reaches the cache, results or logs.
    Returns (groups, dropped mappings)."""
    if not threads:
        return groups, []
    labels = "\n".join(f"[{i}] ({g['kind']}) {g['label']}" for i, g in enumerate(groups))
    listing = "\n".join(f"[{i}] {_thread_text(t)}\n    snippet: {t.get('snippet') or ''}" for i, t in enumerate(threads))

    def ground(reply):
        for m in reply.get("maps") or []:
            i = m.get("thread")
            m["grounded"] = isinstance(i, int) and 0 <= i < len(threads) and \
                _grounded(m.pop("words", "") or "", _mapping_text(threads[i]), 2)
        return reply
    llm = _with_cache(llm, "groq_receipts_forums", sanitize=ground, links=[t.get("link") for t in threads])
    try:
        maps = llm(FORUM_PROMPT.format(name=name, labels=labels or "(none)"), listing).get("maps") or []
    except Abort:
        raise
    except Exception:  # noqa: BLE001 — no forum mapping beats a crash
        return groups, [{"why": "forum mapping failed"}]
    new, dropped, used = {}, [], set()
    for m in maps:
        i, lab = m.get("thread"), m.get("label")
        miss = lambda why: dropped.append({"thread": i, "label": lab, "why": why})
        if not isinstance(i, int) or not 0 <= i < len(threads) or i in used:
            miss("cited no valid thread")
        elif not m.get("grounded"):
            miss("words not in the thread title/URL/snippet")
        elif ROUNDUP.search(threads[i].get("title") or ""):
            miss("roundup title: names no specific symptom")
        elif isinstance(lab, int) and 0 <= lab < len(groups) and groups[lab]["kind"] == "issue":
            groups[lab].setdefault("forums", []).append({**_forum_src(threads[i]), "_thread": threads[i]})
            used.add(i)
        elif isinstance(lab, str) and lab.lower().startswith("new:") and not _is_vague(lab[4:], lab[4:]):
            key = _norm(lab[4:])
            if key not in new:
                new[key] = {"kind": "issue", "label": lab[4:].strip().lower(), "points": [], "forums": []}
                groups.append(new[key])
            new[key]["forums"].append({**_forum_src(threads[i]), "_thread": threads[i]})
            used.add(i)
        else:
            miss("label not a video issue or a concrete new label")
    return groups, dropped


def _amazon_insights(amazon):
    s = (((amazon or {}).get("reviews_information") or {}).get("summary") or {})
    return s.get("insights") or []


def map_marketplace(groups, amazon, name, llm=_groq):
    """Attach Amazon review insights to existing video groups only (g["marketplace"]), as corroboration: they never
    create a group or count as a source. Sentiment must fit: positive -> praise, negative -> issue, mixed -> either.
    ponytail: Amazon only. The cached eBay rows carry seller feedback (and only for the AirPods chips); add eBay
    review highlights when a product row has them."""
    ins = _amazon_insights(amazon)
    video = [g for g in groups if g["points"]]
    if not ins or not video:
        return groups, []
    labels = "\n".join(f"[{i}] ({g['kind']}) {g['label']}" for i, g in enumerate(video))
    listing = "\n".join(f"[{i}] {x['title']} ({x.get('sentiment')}): {x.get('summary')}" for i, x in enumerate(ins))
    llm = _with_cache(llm, "groq_receipts_market", asin=((amazon.get("search_parameters") or {}).get("asin")))
    try:
        maps = llm(MARKET_PROMPT.format(name=name, labels=labels), listing).get("maps") or []
    except Abort:
        raise
    except Exception:  # noqa: BLE001
        return groups, [{"why": "marketplace mapping failed"}]
    fits = {"positive": {"praise"}, "negative": {"issue"}, "mixed": {"praise", "issue"}}
    dropped = []
    for m in maps:
        i, lab = m.get("insight"), m.get("label")
        if not (isinstance(i, int) and 0 <= i < len(ins) and isinstance(lab, int) and 0 <= lab < len(video)):
            dropped.append({**m, "why": "cited no valid insight/label"})
            continue
        x = ins[i]
        if not _grounded(m.get("words") or "", f"{x['title']} {x.get('summary')}", 1):
            dropped.append({**m, "why": "words not in the insight"})
        elif video[lab]["kind"] not in fits.get(x.get("sentiment"), set()):
            dropped.append({**m, "why": f"{x.get('sentiment')} insight can't back a {video[lab]['kind']}"})
        else:
            video[lab].setdefault("marketplace", []).append(
                {"site": "Amazon", "title": x["title"], "sentiment": x.get("sentiment"), "mentions": x.get("mentions"),
                 "links": [e.get("link") for e in x.get("examples") or []][:2]})
    return groups, dropped


def _video_src(p):
    return {"type": "video", "channel": p.get("channel"), "video_id": p["video_id"], "model": p.get("model", "confirmed"),
            "timestamp_s": p["segment_start_s"], "quote": p.get("quote_display") or p["quote"],
            "link": f"https://www.youtube.com/watch?v={p['video_id']}&t={p['segment_start_s']}s"}


def summarize_receipts(product, video_points, forum_threads, marketplace_reviews, transcripts=None, listing_title=None,
                       llm=_groq):
    """product: demo/resolved product (brand, label). video_points: points_for_video results (one per picked video).
    forum_threads: the google_forums response(s). marketplace_reviews: the amazon_product response (or None).
    transcripts: {video_id: transcript response} for the verifier pass. Returns {status, why?, known_issues,
    known_praise, forum_reported, mentioned_once, forum_only, mismatch, audit}. Context only: weight 0.
    Forum sources carry title, site, date and link only: snippets are used inside mapping/verifier calls, never kept."""
    import domains
    target = review_target(product, listing_title)
    if not target:
        return {"status": "unavailable", "why": "no model to match sources on", "known_issues": [], "known_praise": [],
                "forum_reported": [], "mentioned_once": [], "forum_only": [], "mismatch": None}
    name = target_name(target)
    threads = [t for r in (forum_threads if isinstance(forum_threads, list) else [forum_threads])
               for t in (r or {}).get("organic_results") or []]
    threads = list({t.get("link"): t for t in threads}.values())  # "problems" + "issues" queries overlap
    rejected = [{"title": t.get("title"), "why": "search-bait site" if domains.is_search_bait(t.get("link")) else
                 "title doesn't name this model"} for t in threads
                if domains.is_search_bait(t.get("link")) or not forum_names_model(target, t)]
    kept = [t for t in threads if not domains.is_search_bait(t.get("link")) and forum_names_model(target, t)]
    groups = merge_points([p for v in video_points for p in v.get("points") or []], llm=llm)
    groups, forum_dropped = map_forums(groups, kept, name, llm=llm)
    groups, market_dropped = map_marketplace(groups, marketplace_reviews, name, llm=llm)
    groups = verify_groups(groups, transcripts or {}, name, llm=llm)

    def card(g):
        return {"kind": g["kind"], "label": g["label"], "label_display": display_label(g["label"]),
                "type": group_type(g), "source_count": len(_independent(g)),
                "sources": [_video_src(p) for p in g["points"]] +
                           [{"type": "forum", **_forum_src(f)} for f in g.get("forums") or []],
                "marketplace": g.get("marketplace") or [],
                "left_group": [{k: v for k, v in x.items() if k != "_thread"} for x in g.get("left_group") or []]}

    def forum_card(g):
        sites = {domains.registered_domain(f["link"]) for f in g["forums"]}
        return {**card(g), "heading": "reported by owners in forums", "thread_count": len(g["forums"]),
                "sites": len(sites), "single_site": len(sites) < 2}
    known = [card(g) for g in groups if g["known"]]
    had_video = lambda g: any(x.get("video_id") for x in g.get("left_group") or [])  # every video point verified out
    order = lambda c: -c["source_count"]
    # a usable source = a model-confirmed video whose transcript was read, whether or not it raised anything
    confirmed = [v for v in video_points if not v.get("skipped") and not v.get("error")
                 and v.get("model", {}).get("model", "confirmed") == "confirmed"]
    out = {"known_issues": sorted([c for c in known if c["kind"] == "issue"], key=order),
           "known_praise": sorted([c for c in known if c["kind"] == "praise"], key=order),
           "forum_reported": [forum_card(g) for g in groups if g.get("forum_known")],
           "mentioned_once": [card(g) for g in groups if not g["known"] and (g["points"] or had_video(g))],
           "forum_only": [card(g) for g in groups if not g["points"] and not had_video(g)
                          and (g.get("forums") or g.get("left_group")) and not g.get("forum_known")],
           "audit": {"threads": len(threads), "threads_kept": len(kept), "threads_rejected": rejected,
                     "confirmed_videos": len(confirmed), "videos": len(video_points),
                     "forum_dropped": forum_dropped, "market_dropped": market_dropped}}
    if out["known_issues"] or out["known_praise"] or out["forum_reported"]:
        out["status"] = "found"
    elif not confirmed or len(confirmed) + len(kept) < 2:  # "none" needs a review actually read
        out["status"] = "unavailable"
        out["why"] = (f"{len(confirmed)} of {len(video_points)} review videos confirmed as {name}; "
                      f"{len(kept)} forum threads name it")
    else:
        out["status"] = "none"
    # Context only, weight 0, not shown: does a high marketplace rating sit next to known issues? Measure the spread.
    pr = (marketplace_reviews or {}).get("product_results") or {}
    out["mismatch"] = {"rating": pr.get("rating"), "ratings": pr.get("reviews"),
                       "known_issues": len(out["known_issues"])} if pr.get("rating") else None
    return out


# --- Runtime: the "What owners say" pipeline step -------------------------------------------------------------------
# Per-call timeouts (s). Probe latencies 2026-09-30: YouTube search 2-4 s, transcripts 7-10 s, Google Forums 2-78 s.
# A call past its timeout is abandoned, not cancelled: it still finishes and lands in serpapi_cache for the next run.
TIMEOUTS = {"youtube": 20, "youtube_video_transcript": 30, "google_forums": 60}
THIN_FORUMS = 5  # fewer "<name> problems" threads than this -> also "<name> issues" (as in the probe)
LIVE_NOTE = "about 2-3 minutes"
LIMIT_WHY = "Groq's free-tier limit was reached: try again later"
DOWN_WHY = "the language model couldn't be reached: try again later"


def search_base(target, product):
    """What reviewers and forums call the product: the generation name ("Sony WH-1000XM5"), or for a model-code product
    its own short label when that carries the common number ("Honeywell 360° heater"), else the common name."""
    if target["kind"] == "generation":
        return target["name"]
    num, label = (target["common"] or "").split()[-1:], product.get("label") or ""
    if num and re.search(rf"\b{num[0]}", label) and len(label.split()) <= 5:
        return label
    return target["common"] or target["name"]


def listing_features(amazon, listing=None):
    """The listing's own description, for confirm_model: Amazon title, bullet points and a few spec fields; otherwise
    the listing's title and description (eBay)."""
    if amazon and amazon.get("product_results"):
        pd = amazon.get("product_details") or {}
        return [amazon["product_results"].get("title") or "", *(amazon.get("about_item") or [])] + \
               [f"{k}: {pd[k]}" for k in ("model_number", "special_features", "number_of_speeds", "heat_output") if k in pd]
    return [x for x in ((listing or {}).get("title"), (listing or {}).get("description")) if x]


def _fetch(engine, **params):
    """One SerpApi call through serpapi_cache with its own timeout. Cache-only mode never fetches: a miss raises
    NotCached."""
    import serpapi
    if CACHE_ONLY.get():
        hit = serpapi.cached(engine, **params)
        if not hit:  # serpapi.cached returns [] (not None) on a miss
            raise NotCached(engine)
        return hit
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(1)
    try:
        return pool.submit(serpapi.search, engine, **params).result(timeout=TIMEOUTS[engine])
    finally:
        pool.shutdown(wait=False)


def _forums(base):
    out = [_fetch("google_forums", q=f"{base} problems", gl="us", hl="en")]
    if len(out[0].get("organic_results") or []) < THIN_FORUMS:
        out.append(_fetch("google_forums", q=f"{base} issues", gl="us", hl="en"))
    return out


def run(product, listing, amazon=None, emit=lambda e: None, live=False):
    """The "What owners say" step. Context only, weight 0: reads nothing from timing, feeds nothing back.
    listing: trust's listing (title, ...). amazon: the cached amazon_product response (review insights), or None.
    live=False (the pipeline's automatic run) reads caches only; anything missing gives status "on_request" and the
    user can ask for a live run (live=True, about 2-3 min on Groq's free tier). Any engine error or timeout makes that
    source unavailable. Groq's limit gives "unavailable" with retry=True, never partial groups. Never raises."""
    token, tok0, t0 = CACHE_ONLY.set(not live), GROQ_TOKENS[0], time.monotonic()
    try:
        out = _run(product, listing or {}, amazon, emit)
    except NotCached as e:
        out = {"status": "on_request", "why": f"not checked yet ({e} not cached)",
               "note": f"Searches review videos and forums live: {LIVE_NOTE}"}
    except (GroqLimit, GroqDown) as e:
        log.warning("receipts: %s", e)
        out = {"status": "unavailable", "why": LIMIT_WHY if isinstance(e, GroqLimit) else DOWN_WHY, "retry": True}
    except Exception as e:  # noqa: BLE001 — receipts never break the verdict
        log.exception("receipts failed")
        out = {"status": "unavailable", "why": f"receipts failed: {type(e).__name__}"}
    finally:
        CACHE_ONLY.reset(token)
    return {"known_issues": [], "known_praise": [], "forum_reported": [], "mentioned_once": [], "forum_only": [],
            **out, "groq_tokens": GROQ_TOKENS[0] - tok0, "seconds": round(time.monotonic() - t0, 1)}


def _run(product, listing, amazon, emit):
    from concurrent.futures import ThreadPoolExecutor
    title = listing.get("title")
    target = review_target(product, title)
    if not target:
        return {"status": "unavailable", "why": "no model to match review videos and forums on"}
    base, engines = search_base(target, product), {}
    emit({"type": "start", "engines": ["youtube", "youtube_video_transcript", "google_forums"], "query": base,
          "name": target_name(target)})

    def timed(engine, found, fn, *a, video_id=None, **kw):
        """Run one engine call; record ok / unavailable and emit engine_done {engine, found, ms} (+ video_id)."""
        t, key = time.monotonic(), f"{engine} {video_id}" if video_id else engine
        r = None
        try:
            r = fn(*a, **kw)
            engines[key] = "ok"
            return r
        except Abort:
            raise
        except Exception as e:  # noqa: BLE001 — that source is unavailable, never a crash or a penalty
            engines[key] = f"unavailable ({type(e).__name__})"
            return None
        finally:
            if key in engines:  # not on a cache miss: that run is offered on request instead
                emit({"type": "engine_done", "engine": engine, **({"video_id": video_id} if video_id else {}),
                      "found": found(r) if r is not None else 0, "ok": engines[key] == "ok",
                      "ms": int((time.monotonic() - t) * 1000)})
    with ThreadPoolExecutor(4) as pool:
        # pool threads don't inherit CACHE_ONLY on their own: run each in a copy of this context
        sub = lambda fn, *a, **kw: pool.submit(contextvars.copy_context().run, fn, *a, **kw)
        forums_f = sub(timed, "google_forums", lambda rs: sum(len(r.get("organic_results") or []) for r in rs), _forums, base)
        yt = sub(timed, "youtube", lambda r: len(r.get("video_results") or []), _fetch, "youtube",
                 search_query=f"{base} review", gl="us", hl="en").result()
        sel = select_review_videos(product, yt, title) if yt else \
            {"status": "unavailable", "why": "YouTube search unavailable", "picked": [], "rejected": []}
        emit({"type": "videos", "picked": sel["picked"], "rejected_count": len(sel["rejected"])})
        tr_f = {v["video_id"]: sub(timed, "youtube_video_transcript", lambda r: len(r.get("transcript") or []), _fetch,
                                   "youtube_video_transcript", video_id=v["video_id"], v=v["video_id"], language_code="en")
                for v in sel["picked"]}
        transcripts = {vid: f.result() for vid, f in tr_f.items()}
        forums = forums_f.result()
    features = listing_features(amazon, listing)
    video_points = []
    for v in sel["picked"]:  # sequential: the free tier's 8k tokens/min can't take parallel extraction
        tr = transcripts.get(v["video_id"])
        if tr and (tr.get("transcript") or []):
            vp = points_for_video(target, v, tr, features)
            video_points.append(vp)
            emit({"type": "points", "video_id": v["video_id"], "points": vp["points"]})
    out = summarize_receipts(product, video_points, forums or [], amazon, transcripts, listing_title=title)
    missing = [e for e, s in engines.items() if s != "ok"]
    if out["status"] == "unavailable":
        out["why"] = "; ".join([out.get("why") or ""] + [f"{e} {engines[e]}" for e in missing]).strip("; ")
    return {**out, "engines": engines, "query": base, "name": target_name(target),
            "videos": {"status": sel["status"], "why": sel.get("why"), "picked": sel["picked"],
                       "rejected": [{k: r.get(k) for k in ("title", "channel", "why")} for r in sel["rejected"]]}}


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from demo_products import DEMO_PRODUCTS

    OUT = Path(__file__).parent.parent / "phase0" / "receipts_probe" / "out"
    load = lambda name: json.loads((OUT / name).read_text())
    HEATER_TITLE = ("Honeywell HHF360V 360 Degree Surround Fan Forced Heater with Surround Heat Output Charcoal Grey "
                    "Energy Efficient Portable Heater with Adjustable Thermostat & 2 Heat Settings, Small")  # Amazon, pinned

    # Targets: from parsing alone (no review_name on any demo product).
    assert review_target(DEMO_PRODUCTS["sony-xm5"])["name"] == "Sony WH-1000XM5"
    assert review_target(DEMO_PRODUCTS["switch2"])["name"] == "Nintendo Switch 2"
    heater = review_target(DEMO_PRODUCTS["honeywell-heater"], HEATER_TITLE)
    assert (heater["kind"], heater["code"], heater["common"]) == ("code", "HHF360V", "Honeywell 360"), heater
    assert review_target(DEMO_PRODUCTS["honeywell-heater"]) is None  # "Honeywell 360° heater" alone: no code, no number

    runs = {"sony-xm5": select_review_videos(DEMO_PRODUCTS["sony-xm5"], load("sony-xm5_youtube.json")),
            "switch2": select_review_videos(DEMO_PRODUCTS["switch2"], load("switch2_youtube.json")),
            "honeywell-heater": select_review_videos(DEMO_PRODUCTS["honeywell-heater"],
                                                     load("honeywell-heater_youtube.json"), HEATER_TITLE)}
    for k, r in runs.items():
        print(f"\n=== {k}: {r['status']}  target {r['target']['name']}"
              + (f" (common name {r['target']['common']})" if r["target"].get("common") else ""))
        for v in r["picked"]:
            print(f"  PICK  {v['title']!r} | {v['channel']} | {v['views']:,} views | {v['length']} | model {v['model']}")
        for v in r["rejected"]:
            print(f"  reject [{v['why']}] {v['title']!r} | {v['channel']}")
    why = {(k, v["title"]): v["why"] for k, r in runs.items() for v in r["rejected"]}
    pick = lambda k: [v["channel"] for v in runs[k]["picked"]]

    # Every video accounted for, exactly once.
    for k, r in runs.items():
        assert len(r["picked"]) + len(r["rejected"]) == len(load(f"{k}_youtube.json")["video_results"]), k
    # Sony: 2-3 genuine reviews, different channels (ground truth: all G).
    assert 2 <= len(pick("sony-xm5")) <= 3 and len(set(pick("sony-xm5"))) == len(pick("sony-xm5"))
    assert set(pick("sony-xm5")) <= {"Marques Brownlee", "Mike O'Brien", "Mark Ellis Reviews", "6 Months Later",
                                     "SoundGuys", "CNET", "Steven Foster", "Karl Conrad"}
    assert runs["switch2"]["status"] == "found" and runs["honeywell-heater"]["status"] == "found"

    # Each rejection category, on a real title.
    real = {
        ("sony-xm5", "Sony WH-1000XM6 vs Sony WH-1000XM5 - Should you upgrade?"): "comparison",
        ("sony-xm5", "Sony’s WH-1000XM5 hands-on"): "unboxing / first look",
        ("sony-xm5", "Sony Headphones WH-1000XM5 (User Guide)"): "not a review (news / how-to)",
        ("switch2", "Nintendo's Switch 2 Plans Leak Early? & Sony Backs Out of A Big Event | News Wave"):
            "not a review (news / how-to)",
        ("sony-xm5", "Sony WH-1000XM6 Review: Back on the Throne!"): "other model (WH-1000XM6)",
        ("honeywell-heater", "Honeywell 360 Surround Heat Heater HZ-0360 REVIEW"): "other model (HZ-0360)",
        ("sony-xm5", "Sony WF-1000XM5 Earbuds Review - 6 Months Later"): "doesn't name the Sony WH-1000XM5 (other product)",
        ("honeywell-heater", "Honeywell Safeguard Motion Sensor Ceramic Space Heater Review"):
            "doesn't name the Honeywell 360 (other product)",
        ("switch2", "EA Sports FC 27 Nintendo Switch 2 Review"): "product isn't the subject of the title",
        ("switch2", "Minecraft Dungeons 2 on Nintendo Switch 2 — Worth It?"):
            "product isn't the subject of the title",
        ("switch2", "10 NEW Nintendo Switch 2 eShop Games Worth Buying!"):
            "product isn't the subject of the title",
        ("switch2", "Let's talk about Minecraft Dungeons 2 on Switch 2... Is it GOOD?"): "doesn't name Nintendo",
        ("sony-xm5", "I've been SPOILED! - Sony WH-1000XM5"): "not titled as a review",
        ("honeywell-heater", "Honeywell HHF360VWMC1 360 Surround Heater Slate Review, Great for small rooms"): "short (1:13)",
        ("honeywell-heater", "Honeywell 360° Digital Surround Heater HHF370 - Safety Features"): "maker's own channel",
        ("sony-xm5", "Sony WH-1000XM5 - 3 Years Later Review"): "same channel as a better-ranked video (SoundGuys)",
    }
    for key, want in real.items():
        assert why.get(key) == want, (key, why.get(key), want)
    assert any(w.startswith("spare") for w in why.values())
    # No real title in the probe is a deal/sale/giveaway video: synthetic.
    fake = {"title": "Sony WH-1000XM5 Review - Prime Day DEAL!", "channel": {"name": "X"}, "length": "8:00", "views": 1}
    assert _reject_reason(runs["sony-xm5"]["target"], fake, re.compile("^Sony")) == "deal / sale / giveaway"
    # Unboxing that is also a review is kept.
    assert not why.get(("sony-xm5", "Sony WH-1000XM5 UNBOXING + REVIEW - The new King of Noise Cancelling!"), "").startswith("unbox")
    # Fewer than 2 usable -> unavailable, not an error.
    one = {"video_results": load("sony-xm5_youtube.json")["video_results"][:1]}
    assert select_review_videos(DEMO_PRODUCTS["sony-xm5"], one)["status"] == "unavailable"
    assert select_review_videos({"brand": None}, one)["status"] == "unavailable"

    # confirm_model grounding (stub LLM; the live check is --live).
    feats = ["Honeywell HHF360V 360 Degree Surround Heater, adjustable thermostat, 2 heat settings", "special_features: Manual"]
    sheryl = load("honeywell-heater_transcript_1gmhM-sogL4.json")
    vid = {"title": "Honeywell 360 Space Heater Review"}
    grounded = lambda *a: {"verdict": "different_model", "feature": "digital thermostat buttons", "segment": 12,
                           "quote": "you can also set your thermostat with the up down buttons"}
    got = confirm_model(heater, vid, sheryl, feats, llm=grounded)
    assert got["model"] == "rejected" and got["evidence"]["start_time_text"] == "1:36", got
    invented = lambda *a: {"verdict": "different_model", "feature": "remote", "segment": 12, "quote": "comes with a remote control"}
    assert confirm_model(heater, vid, sheryl, feats, llm=invented)["model"] == "not confirmed"
    assert confirm_model(heater, vid, sheryl, feats, llm=lambda *a: {**grounded(), "segment": 999})["model"] == "not confirmed"
    assert confirm_model(heater, vid, sheryl, feats, llm=lambda *a: 1 / 0)["model"] == "not confirmed"
    assert confirm_model(heater, vid, {"error": "no transcript"}, feats, llm=grounded)["model"] == "not confirmed"
    assert confirm_model(heater, {"title": "Honeywell HHF360V review"}, sheryl, feats, llm=grounded)["model"] == "confirmed"
    spoken = {"transcript": [{"start_ms": 0, "snippet": "this is the honeywell hhf370 digital model", "start_time_text": "0:00"}]}
    assert confirm_model(heater, vid, spoken, feats, llm=grounded)["model"] == "rejected"  # names another code aloud
    skywind = load("honeywell-heater_transcript_qBAFwTnaLI4.json")  # says "sky1007" (its channel), not a model code
    assert confirm_model(heater, vid, skywind, feats, llm=lambda *a: {"verdict": "no_conflict"})["model"] == "not confirmed"

    # Sponsor filter, on the real reads.
    ltt, mkb_sw, fowler = (load(f"switch2_transcript_{v}.json") for v in ("58XieqW6CO4", "-NNCca97v98", "Qviwd67iz5s"))
    ads = sponsor_segments(ltt)
    assert {6, 7, 8} <= ads and 11 not in ads, ads  # dbrand intro ends at "...small.com"; 1:24 drift line stays
    assert set(range(110, 121)) <= ads and 109 not in ads  # outro read (14:41 "JoyCons not super comfortable")
    assert sponsor_segments(mkb_sw) == set(range(105, 121))  # "channel sponsored Dbrand" -> "Link will be below"
    assert set(range(19, 34)) <= sponsor_segments(fowler)  # "Creator micro sponsored break" chapter
    assert not sponsor_segments(load("sony-xm5_transcript_6CsJZxfZsL0.json")) and not sponsor_segments(sheryl)

    # locate_point grounding, on MKBHD's real segments (stub LLM output).
    mk = load("sony-xm5_transcript_6CsJZxfZsL0.json")
    mkbhd = _clean_segs(mk)
    fold = {"kind": "issue", "label": "doesn't fold", "thing": "folding hinge", "segment": 19, "anchor": "does not fold at all"}
    got, why = locate_point(fold, mkbhd)
    assert (got["segment_start_s"], got["how"], got["match"]) == (135, "cited", 1.0), (got, why)
    assert got["quote"] == "This new version with this new design does not fold at all.", got  # original casing
    assert len(locate_point({**fold, "anchor": "and fit into their little case just like that"}, mkbhd)[0]["quote"].split()) <= 15
    assert locate_point({**fold, "segment": 18}, mkbhd)[0]["segment_start_s"] == 135  # anchor in the next segment
    re_anc = locate_point({**fold, "segment": 10}, mkbhd)[0]  # wrong segment, unique elsewhere: re-anchored to 2:15
    assert (re_anc["how"], re_anc["segment_start_s"]) == ("re-anchored", 135), re_anc
    assert locate_point({**fold, "anchor": "the king of the hill", "segment": 5}, mkbhd)[1] == "anchor ambiguous (3 places)"
    assert locate_point({**fold, "anchor": "does not folds at all"}, mkbhd)[0]["match"] >= FUZZY_MIN  # ASR-size slip
    assert locate_point({**fold, "anchor": "this new design ... does not fold"}, mkbhd)[1] == "elided anchor"
    assert locate_point({**fold, "anchor": "the headband creaks every time"}, mkbhd)[1] == "anchor not in transcript"
    assert locate_point({**fold, "anchor": "not fold"}, mkbhd)[1] == "anchor not 4-15 words"
    assert locate_point({**fold, "segment": 999}, mkbhd)[1] == "cited segment invalid"
    assert locate_point({**fold, "label": "not perfect", "thing": "overall"}, mkbhd)[1] == "vague"
    assert locate_point({**fold, "label": "some people might not like it", "thing": "it"}, mkbhd)[1] == "vague"
    tough = {"kind": "issue", "label": "screen fragile", "thing": "screen", "segment": 8, "anchor": "The screen on the Switch 2 is not tough"}
    assert locate_point(tough, _clean_segs(ltt))[1] == "anchor not in transcript"  # inside the dbrand read
    stub = lambda *a, it=iter([{"points": [fold, {**fold, "segment": 10}, {**fold, "anchor": "the king of the hill"}]}]): \
        next(it, {"points": []})  # MKBHD is 2 chunks; the stub answers the first
    ep = extract_points({"video_id": "6CsJZxfZsL0"}, mk, "Sony WH-1000XM5", llm=stub)
    assert len(ep["points"]) == 1 and ep["dropped"] == {"duplicate label": 1, "anchor ambiguous (3 places)": 1}, ep
    sent = []
    extract_points({"video_id": "58XieqW6CO4"}, ltt, "Nintendo Switch 2", llm=lambda s, u: sent.append(u) or {"points": []})
    assert "not tough" not in "".join(sent) and "Joy-Con controllers, which appear" in "".join(sent)  # LLM never sees ads
    assert extract_points({"video_id": "x"}, {"error": "none"}, llm=stub)["error"] == "no transcript"
    assert extract_points({"video_id": "x"}, mk, llm=lambda *a: 1 / 0)["points"] == []
    # A video confirm_model rejects (Sheryl's digital HHF370) contributes no points.
    never = lambda *a: {"points": [{**fold, "segment": 0, "anchor": "hello and welcome to shopping"}]}
    sv = points_for_video(heater, {"video_id": "1gmhM-sogL4", "title": "Honeywell 360 Space Heater Review"}, sheryl, feats,
                          llm=lambda s, u: grounded() if s == FEATURE_PROMPT else never())
    assert sv["points"] == [] and sv["skipped"]["model"] == "rejected", sv
    # merge_points: never mixes kinds, keeps every point and its quote exactly once.
    pts = [{"kind": "issue", "label": "a", "quote": "q1"}, {"kind": "praise", "label": "b", "quote": "q2"},
           {"kind": "issue", "label": "c", "quote": "q3"}]
    mg = merge_points(pts, llm=lambda *a: {"groups": [{"label": "x", "members": [0, 1, 2, 0, 7]}]})
    assert sorted(q["quote"] for g in mg for q in g["points"]) == ["q1", "q2", "q3"]
    assert all(len({q["kind"] for q in g["points"]}) == 1 for g in mg)
    assert len(merge_points(pts, llm=lambda *a: 1 / 0)) == 3
    # verify_groups: only 2+ source groups are checked; a failed point leaves; the group loses "known".
    a, b = {**got, "video_id": "A"}, {**got, "video_id": "B", "segment": 40}
    trs = {"A": mk, "B": mk}
    def answers(user, say=lambda pid, body: "yes", words=lambda pid, body: ""):
        """Stub batched verifier: one answer per '### <id>' item, citing the item's first segment."""
        out = []
        for pid, body in re.findall(r"### (\w+) \([^)]*\)\n(.*?)(?=\n\n### |\Z)", user, re.S):
            seg = re.search(r"\[(\d+)\]", body)
            out.append({"point_id": pid, "supports": say(pid, body), "cited_segment": seg and int(seg.group(1)),
                        "words": words(pid, body)})
        return {"answers": out}
    vg = lambda ans: verify_groups([{"kind": "issue", "label": "doesn't fold", "points": [a, b]}], trs, "Sony", llm=ans)[0]
    calls = []
    assert vg(lambda s, u: calls.append(u) or answers(u))["known"] and len(calls) == 1  # one call per group
    g = vg(lambda s, u: answers(u, say=lambda pid, body: "no" if "[40]" in body else "yes"))
    assert (g["known"], len(g["points"]), len(g["left_group"])) == (False, 1, 1), g
    assert not vg(lambda s, u: {"answers": [{**x, "cited_segment": 80} for x in answers(u)["answers"]]})["known"]
    left = vg(lambda s, u: {"answers": answers(u)["answers"][:1]})["left_group"]  # v1 never answered
    assert [x["why"] for x in left] == ["verifier didn't answer it"], left
    dup = vg(lambda s, u: {"answers": answers(u)["answers"] + answers(u)["answers"][:1]})
    assert not dup["known"] and dup["left_group"][0]["why"] == "verifier answered it twice", dup
    assert not vg(lambda s, u: {"answers": [{**x, "supports": "maybe"} for x in answers(u)["answers"]]})["known"]
    assert not vg(lambda s, u: {"answers": ["v0", None, 3]})["known"]  # malformed entries
    assert not vg(lambda *a: 1 / 0)["known"]
    try:  # Groq's limit stops the whole run: never a partial group
        vg(lambda *a: (_ for _ in ()).throw(GroqLimit("per day")))
        raise AssertionError("GroqLimit swallowed")
    except GroqLimit:
        pass
    single = verify_groups([{"kind": "issue", "label": "x", "points": [a]}], trs, "Sony", llm=lambda *a: 1 / 0)[0]
    assert single["points"] == [a] and not single["known"]  # not checked, never known

    # Forums: titles naming this product, on the real results.
    sony_t, sw_t = runs["sony-xm5"]["target"], runs["switch2"]["target"]
    fsony = load("sony-xm5_forums_problems.json")["organic_results"]
    fsw = load("switch2_forums_problems.json")["organic_results"]
    keep = lambda t, rows: {r["title"] for r in rows if forum_names_model(t, r)}
    ks = keep(sony_t, fsony)
    assert "XM5 Headphone Design Flaw" in ks and "WH-1000XM5 keeps shutting down randomly after a \"pop\"." in ks  # no brand
    assert "Sony SH1000XM5 Hinge and Warranty" not in ks  # misspelt model: not this product by its title
    assert len(keep(sw_t, fsw)) == 10  # incl. Quora's cut title, via its URL "...nintendo-switch-2-besides-stick-drift"
    assert not forum_names_model(sony_t, {"title": "WH-1000XM6 vs XM5 hinge", "link": "x"})
    assert not forum_names_model(sony_t, {"title": "WH-1000XM4 headband cracked", "link": "x"})
    assert not forum_names_model(sw_t, {"title": "Switch OLED joy-con drift", "link": "x"})
    # map_forums: every mapping cites a thread and words found in it.
    vg0 = [{"kind": "issue", "label": "joy-con drift", "points": []}, {"kind": "praise", "label": "bigger screen", "points": []}]
    quora = next(r for r in fsw if "quora" in r["link"])
    assert ROUNDUP.search(quora["title"])  # "What are some other hardware flaws…": a roundup, never a source
    quora = {"title": "Switch 2 joy-con stick drift after a month", "source": "Reddit", "date": None,
             "link": "https://www.reddit.com/r/x/comments/2/switch_2_joycon_stick_drift_after_a_month",
             "snippet": "my left stick started to drift besides stick drift nothing else"}  # a specific report
    rows = [quora, fsw[0]]
    fm = lambda maps: map_forums([dict(g) for g in vg0], rows, "Nintendo Switch 2", llm=lambda *a: {"maps": maps})
    gs, dr = fm([{"thread": 0, "label": 0, "words": "besides stick drift"}])
    assert gs[0]["forums"][0]["link"] == quora["link"] and not dr
    assert not fm([{"thread": 0, "label": 0, "words": "joysticks broke after a week"}])[0][0].get("forums")  # not in title
    assert fm([{"thread": 7, "label": 0, "words": "stick drift"}])[1][0]["why"] == "cited no valid thread"
    assert fm([{"thread": 0, "label": 1, "words": "stick drift"}])[1][0]["why"].startswith("label not")  # praise
    assert fm([{"thread": 0, "label": "new: problems", "words": "stick drift"}])[1]  # vague new label
    gs = fm([{"thread": 0, "label": "new: stick drift", "words": "stick drift after"}])[0]
    assert gs[-1]["label"] == "stick drift" and gs[-1]["points"] == []
    # Recurrence rule (stub LLM for merge/verify).
    pt = lambda vid, model="confirmed", kind="issue": {"kind": kind, "label": "x", "quote": "q", "segment_start_s": 1,
                                                       "segment": 1, "video_id": vid, "channel": vid, "model": model}
    g = lambda pts, forums=(), market=(): {"kind": "issue", "label": "x", "points": pts, "forums": list(forums),
                                           "marketplace": list(market)}
    f1, f2 = {"link": "f1"}, {"link": "f2"}
    assert _is_known(g([pt("A"), pt("B")])) and _is_known(g([pt("A")], [f1]))
    assert not _is_known(g([pt("A"), pt("A")]))  # same video twice
    assert not _is_known(g([], [f1, f2]))  # forums alone
    assert not _is_known(g([pt("A")], market=[{"site": "Amazon"}]))  # marketplace never counts
    assert not _is_known(g([pt("A"), pt("B", "not confirmed")]))  # unconfirmed model doesn't count
    # summarize_receipts end to end, stub LLM, real forum + Amazon-shaped data.
    amazon = {"product_results": {"rating": 4.5, "reviews": 5595}, "search_parameters": {"asin": "T"},
              "reviews_information": {"summary": {"insights": [
                  {"title": "Battery life", "sentiment": "mixed", "summary": "mixed battery life", "examples": []},
                  {"title": "Screen size", "sentiment": "positive", "summary": "love the screen", "examples": []}]}}}
    def stub(system, user):
        if system == MERGE_PROMPT:
            return {"groups": [{"label": "joy-con drift", "members": [0, 1]}]}
        if system.startswith("Review videos") and "forum" in system:
            return {"maps": [{"thread": 0, "label": 0, "words": "stick drift"}]}
        if system.startswith("Review videos"):  # marketplace: a positive insight can't back an issue
            return {"maps": [{"insight": 1, "label": 0, "words": "screen"}]}
        return {"answers": [{**a, "same_symptom": "yes" if a["point_id"].startswith("t") else ""}
                            for a in answers(user, words=lambda pid, body: "stick drift" if pid.startswith("t") else "")["answers"]]}
    vp = [{"video_id": "A", "model": {"model": "confirmed"}, "points": [pt("A")]},
          {"video_id": "B", "model": {"model": "not confirmed"}, "points": [pt("B", "not confirmed")]}]
    tr = {"A": {"transcript": [{"start_ms": 0, "snippet": "x"}] * 3}}
    out = summarize_receipts(DEMO_PRODUCTS["switch2"], vp, {"organic_results": rows}, amazon, tr, llm=stub)
    ki = out["known_issues"]
    assert out["status"] == "found" and len(ki) == 1 and ki[0]["source_count"] == 2, out  # video A + Quora thread
    assert {s["type"] for s in ki[0]["sources"]} == {"video", "forum"} and not ki[0]["marketplace"]
    assert out["audit"]["market_dropped"][0]["why"] == "positive insight can't back a issue"
    assert out["mismatch"] == {"rating": 4.5, "ratings": 5595, "known_issues": 1}
    none = summarize_receipts(DEMO_PRODUCTS["switch2"], vp, {"organic_results": rows}, None, tr,
                              llm=lambda s, u: {"groups": [], "maps": []} if s == MERGE_PROMPT or "Review videos" in s
                              else answers(u))
    assert none["status"] == "none" and none["mentioned_once"], none
    thin = summarize_receipts(DEMO_PRODUCTS["honeywell-heater"], vp[1:], {"error": "no results"}, None, tr,
                              listing_title=HEATER_TITLE, llm=stub)
    assert thin["status"] == "unavailable" and "0 of 1" in thin["why"], thin
    assert summarize_receipts({"brand": None}, [], None, None)["status"] == "unavailable"
    # Transcripts all failed, forum threads exist: no review was read, so never "none".
    no_videos = summarize_receipts(DEMO_PRODUCTS["switch2"], [], {"organic_results": rows}, None, tr,
                                   llm=lambda s, u: {"groups": [], "maps": []} if s == MERGE_PROMPT or "Review videos" in s
                                   else answers(u))
    assert no_videos["status"] == "unavailable" and "0 of 0 review videos" in no_videos["why"], no_videos

    # Rev 3: 15-word anchors; search bait; forum-only rule; snippets used for mapping, never kept.
    assert locate_point({**fold, "anchor": "This new version with this new design does not fold at all."}, mkbhd)[0]
    flaw, latvia = (next(r for r in fsony if r["title"].startswith(x)) for x in ("XM5 Headphone Design", "WH-1000XM5 Broke"))
    third = {"title": "WH-1000XM5 headband snapped", "link": "https://www.reddit.com/r/x/comments/1/xm5", "source": "Reddit",
             "snippet": "the headband snapped at the swivel joint"}
    bait = next(r for r in fsony if "justanswer" in r["link"])
    def fstub(n_threads, verdict="yes"):
        def llm(system, user):
            if system == MERGE_PROMPT:
                return {"groups": []}
            if system.startswith("Review videos") and "forum" in system:
                return {"maps": [{"thread": i, "label": "new: headband breaks", "words": w} for i, w in
                                 enumerate(["headband swivel", "hinge broke", "headband snapped"][:n_threads])]}
            if system.startswith("You check one claim"):
                return {"answers": [{**a, "same_symptom": "yes"} for a in answers(user, say=lambda pid, body: verdict, words=lambda pid, body: next(
                    w for w in ("headband swivel", "hinge broke", "headband snapped") if w in body.lower()))["answers"]]}
            return {"maps": []}
        return llm
    rows3 = {"organic_results": [flaw, latvia, third, bait]}
    out3 = summarize_receipts(DEMO_PRODUCTS["sony-xm5"], [], rows3, None, llm=fstub(3))
    fr = out3["forum_reported"]
    assert len(fr) == 1 and fr[0]["thread_count"] == 3 and fr[0]["sites"] == 3 and out3["status"] == "found", out3
    assert fr[0]["heading"] == "reported by owners in forums" and not out3["known_issues"]  # never reviewer consensus
    assert {"title": bait["title"], "why": "search-bait site"} in out3["audit"]["threads_rejected"]
    dumped = json.dumps(out3)
    assert all(r["snippet"][:40] not in dumped for r in (flaw, latvia, third)) and "snippet" not in dumped
    assert set(fr[0]["sources"][0]) == {"type", "title", "link", "source", "date"}
    two = summarize_receipts(DEMO_PRODUCTS["sony-xm5"], [], {"organic_results": [flaw, latvia]}, None, llm=fstub(2))
    assert not two["forum_reported"] and two["forum_only"][0]["source_count"] == 2  # 2 threads: listed, not known
    nope = summarize_receipts(DEMO_PRODUCTS["sony-xm5"], [], rows3, None, llm=fstub(3, "no"))
    assert not nope["forum_reported"] and len(nope["forum_only"][0]["left_group"]) == 3  # verifier removes all
    related = {"kind": "issue", "label": "headband breaks", "points": [], "forums": [{**_forum_src(latvia), "_thread": latvia}]}
    said = lambda ss: lambda s, u: {"answers": [{"point_id": "t0", "supports": "yes", "same_symptom": ss, "words": "hinge broke"}]}
    assert verify_group("Sony", related, {}, llm=said("no"))["t0"] == (False, "verifier: different symptom")
    assert verify_group("Sony", related, {}, llm=said(""))["t0"] == (False, "verifier: different symptom")  # must be explicit
    assert verify_group("Sony", related, {}, llm=said("yes"))["t0"] == (True, "verifier: yes")
    snip_only = map_forums([], [latvia], "Sony", llm=lambda *a: {"maps": [{"thread": 0, "label": "new: hinge breaks",
                                                                             "words": "Left side hinge broke"}]})
    assert snip_only[0][0]["forums"] and "snippet" not in snip_only[0][0]["forums"][0]  # grounded on the snippet only

    # Runtime: run() never raises; sources fail independently; cache misses -> on_request; Groq's limit -> try later.
    real_fetch, real_pfv = _fetch, points_for_video
    sony_listing = {"title": "Sony WH-1000XM5 Premium Noise Cancelling Wireless Headphones"}
    probe = {("youtube", None): load("sony-xm5_youtube.json"), ("google_forums", None): load("sony-xm5_forums_problems.json")}
    def fake_fetch(fail=(), uncached=()):
        def f(engine, **params):
            if engine in uncached and CACHE_ONLY.get():
                raise NotCached(engine)
            if engine in fail:
                raise TimeoutError(engine)
            if engine == "youtube_video_transcript":
                path = OUT / f"sony-xm5_transcript_{params['v']}.json"
                return load(path.name) if path.exists() else {"error": "no transcript"}
            return probe[(engine, None)]
        return f
    no_points = lambda target, v, tr, feats, llm=None: {"video_id": v["video_id"], "points": [], "model": {"model": "confirmed"}}
    try:
        points_for_video = no_points
        assert run({"brand": None, "label": "thing"}, {})["status"] == "unavailable"  # no parsable model
        _fetch = fake_fetch(uncached=("youtube_video_transcript",))
        r = run(DEMO_PRODUCTS["sony-xm5"], sony_listing)  # cache-only: a transcript missing
        assert r["status"] == "on_request" and "2-3 minutes" in r["note"], r
        _fetch = fake_fetch(fail=("youtube", "google_forums"))
        r = run(DEMO_PRODUCTS["sony-xm5"], sony_listing, live=True)  # both engines down
        assert r["status"] == "unavailable" and "youtube unavailable (TimeoutError)" in r["why"], r
        assert r["engines"]["google_forums"].startswith("unavailable") and not r["known_issues"]
        _fetch = fake_fetch(fail=("google_forums",))
        events = []
        r = run(DEMO_PRODUCTS["sony-xm5"], sony_listing, emit=events.append, live=True)  # forums down, videos fine
        assert r["engines"]["youtube"] == "ok" and len(r["videos"]["picked"]) == 3 and r["status"] == "none", r
        types = [e["type"] for e in events]
        assert types[0] == "start" and {"videos", "engine_done"} <= set(types) and "points" in types, types
        done = {(e["engine"], e.get("video_id")): e for e in events if e["type"] == "engine_done"}
        assert done[("youtube", None)]["found"] == len(load("sony-xm5_youtube.json")["video_results"]) > 0
        assert not done[("google_forums", None)]["ok"] and done[("google_forums", None)]["found"] == 0
        assert done[("youtube_video_transcript", "6CsJZxfZsL0")]["found"] == 86 and "ms" in done[("youtube", None)]
        assert next(e for e in events if e["type"] == "videos")["rejected_count"] == len(r["videos"]["rejected"])
        def limited(*a, **k):
            raise GroqLimit("tokens per day")
        _fetch, points_for_video = fake_fetch(), limited
        r = run(DEMO_PRODUCTS["sony-xm5"], sony_listing, live=True)
        assert (r["status"], r["why"], r.get("retry"), r["known_issues"]) == ("unavailable", LIMIT_WHY, True, []), r
        def down(*a, **k):
            raise GroqDown("ConnectError")
        points_for_video = down
        r = run(DEMO_PRODUCTS["sony-xm5"], sony_listing, live=True)
        assert (r["status"], r["why"], r.get("retry"), r["mentioned_once"]) == ("unavailable", DOWN_WHY, True, []), r
        points_for_video = lambda *a, **k: 1 / 0  # any other failure: unavailable, never an exception
        assert run(DEMO_PRODUCTS["sony-xm5"], sony_listing, live=True)["status"] == "unavailable"
    finally:
        _fetch, points_for_video = real_fetch, real_pfv
    real_post, had_key = httpx.post, "GROQ_API_KEY" in os.environ
    os.environ.setdefault("GROQ_API_KEY", "test")  # removed again below: --points must load the real key from .env
    httpx.post = lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down"))
    try:
        _groq("s", "u")
        raise AssertionError("GroqDown not raised")
    except GroqDown:
        pass
    finally:
        httpx.post = real_post
        if not had_key:
            del os.environ["GROQ_API_KEY"]
    for raw, want in [("no usb‑c audio", "No USB-C audio"), ("lcd vs oled drawbacks", "LCD vs OLED"),
                      ("joy‑con drift issues", "Joy-Con drift"), ("strong anc performance", "Strong ANC performance"),
                      ("120\u202fhz refresh", "120 Hz refresh"), ("display quality praise", "Display quality"),
                      ("non‑foldable design", "Non-foldable design"), ("vrr not via hdmi", "VRR not via HDMI"),
                      ("High Price", "High price"), ("usb-a ports odd", "USB-A ports odd")]:
        assert display_label(raw) == want, (raw, display_label(raw), want)
    assert [issue_type("issue", x) for x in ("joy-con drift", "headband breaks", "high price", "lcd vs oled")] == \
        ["problem", "problem", "criticism", "criticism"] and issue_type("praise", "drift") == "praise"
    # Speculation vs report, on the real drift segments (Switch 2): all three are risks, none reports drift.
    sw_segs = {v: _clean_segs(load(f"switch2_transcript_{v}.json")) for v in ("-NNCca97v98", "58XieqW6CO4", "Qviwd67iz5s")}
    seg_t = lambda v, t: next(s["snippet"] for s in sw_segs[v] if s["start_time_text"] == t)
    assert is_hedged(seg_t("-NNCca97v98", "10:41"))  # "So will these also start to have drift issues over time?"
    assert is_hedged(seg_t("58XieqW6CO4", "1:24"))   # "appear to still be susceptible to drifting"
    assert is_hedged(seg_t("Qviwd67iz5s", "6:15"))   # "The joysticks still may have to deal with drift eventually"
    assert is_hedged("the same tech that caused the entire joystick drift disaster on Switch One is still present")
    assert not is_hedged("my left joy-con started to drift after two weeks")  # reported, even with "could" around it
    assert not is_hedged("mine developed drift, it could not be fixed") and not is_hedged("This new design does not fold at all.")
    drift = lambda hedges, forums=(): {"kind": "issue", "label": "joy-con drift", "forums": list(forums),
                                       "points": [{"video_id": f"v{i}", "hedged": h} for i, h in enumerate(hedges)]}
    assert group_type(drift([True, True, True])) == "concern"
    assert group_type(drift([True, False, True])) == "concern"  # one report is not two
    assert group_type(drift([False, False])) == "problem"
    assert group_type(drift([True, False], [{"link": "t"}])) == "problem"  # a video report + a forum thread
    assert group_type({"kind": "issue", "label": "high price", "points": [{"video_id": "a", "hedged": True}]}) == "criticism"
    # Roundup titles never count as a source; specific titles do.
    roundups = ["What are the common issues with switch 2?", "Common issues with Nintendo Switch 2?",
                "Any known early defects that have showed up from Switch 2 ...", "Problems with the WH-1000XM5?",
                "What are some other hardware flaws plaguing the ..."]
    specific = ["Sony wh-1000xm5 weird noise issue?", "Overheating Issues With WH-1000XM5 Headphones",
                "XM5 Headphone Design Flaw", "Topic: Is the Switch 2 screen really that bad?",
                "WH-1000XM5 keeps shutting down randomly after a \"pop\"."]
    assert all(ROUNDUP.search(t) for t in roundups) and not any(ROUNDUP.search(t) for t in specific)
    reddit = next(r for r in fsw if r["title"].startswith("What are the common"))
    gs, dr = map_forums([{"kind": "issue", "label": "refresh rate lock", "points": []}], [reddit], "Nintendo Switch 2",
                        llm=lambda *a: {"maps": [{"thread": 0, "label": 0, "words": "refresh quite as fast"}]})
    assert not gs[0].get("forums") and dr[0]["why"] == "roundup title: names no specific symptom", (gs, dr)
    # Displayed quote starts at the anchor when the words before it turn ("…here. It is still slim…").
    ltt_segs = _clean_segs(ltt)
    slim = locate_point({"kind": "praise", "label": "slim form factor", "thing": "device thickness", "segment": 86,
                         "anchor": "It is still slim, which is really nice"}, ltt_segs)[0]
    assert slim["quote_display"].startswith("It is still slim") and slim["quote"].startswith("regression"), slim
    assert _video_src({**slim, "video_id": "58XieqW6CO4"})["quote"].startswith("It is still slim")
    assert locate_point(fold, mkbhd)[0]["quote_display"] == "This new version with this new design does not fold at all."
    assert search_base(heater, DEMO_PRODUCTS["honeywell-heater"]) == "Honeywell 360° heater"
    assert search_base(runs["switch2"]["target"], DEMO_PRODUCTS["switch2"]) == "Nintendo Switch 2"

    def amazon_row(asin):
        from serpapi import db
        return db.table("serpapi_cache").select("response_json").eq("engine", "amazon_product") \
            .eq("response_json->search_parameters->>asin", asin).execute().data[0]["response_json"]

    def heater_listing():
        return listing_features(amazon_row("B00VV156LA"))

    if "--points" in sys.argv:  # live Groq (Supabase-cached) on the 9 saved transcripts; raw replies to out/points_v2/
        if "--verify-model" in sys.argv:  # the 120b vs 20b verifier comparison: run once per model
            VERIFY_MODEL = sys.argv[sys.argv.index("--verify-model") + 1]
        print(f"verifier model: {VERIFY_MODEL}")
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / ".env")
        mmss = lambda t: f"{t // 60}:{t % 60:02d}"
        ch, vids = {}, {}
        for k in runs:
            for v in load(f"{k}_youtube.json")["video_results"]:
                ch[v["video_id"]] = v["channel"]["name"]
                vids[v["video_id"]] = {"video_id": v["video_id"], "title": v["title"], "channel": v["channel"]["name"],
                                       "link": v["link"]}
        targets = {k: r["target"] for k, r in runs.items()}
        listing = {"honeywell-heater": heater_listing()}
        raw_dir = OUT / "points_v2"
        raw_dir.mkdir(exist_ok=True)
        report, transcripts, timing = {}, {}, []
        for f in sorted(OUT.glob("*_transcript_*.json")):
            k, vid = f.stem.split("_transcript_")
            transcripts[vid] = json.loads(f.read_text())
            n0, t0 = len(LLM_LOG), time.monotonic()
            r = points_for_video(targets[k], vids[vid], transcripts[vid], listing.get(k))
            calls = LLM_LOG[n0:]
            timing.append((k, ch[vid], len(transcripts[vid]["transcript"]), time.monotonic() - t0,
                           sum(c["tokens"] for c in calls), sum(not c["cached"] for c in calls), len(calls)))
            for n, c in enumerate(calls):
                (raw_dir / f"{c['engine']}_{vid}_{n}.json").write_text(json.dumps(c["reply"], indent=1))
            report.setdefault(k, []).append(r)
            if r.get("skipped"):
                print(f"\n=== {k} / {ch[vid]} ({vid}): SKIPPED, confirm_model {r['skipped']}")
                continue
            print(f"\n=== {k} / {ch[vid]} ({vid}) model {r['model']['model']}: {len(r['points'])} kept of "
                  f"{r.get('raw_count')}, dropped {r['dropped'] or 0} {r.get('error') or ''}")
            for p in r["points"]:
                print(f"  {p['kind']:6} {mmss(p['segment_start_s']):>5} {p['label']!r:34} \"{p['quote']}\""
                      + ("" if p["how"] == "cited" else f"  [{p['how']}]") + ("" if p["match"] == 1 else f" [fuzzy {p['match']}]"))
            for d in r["dropped_examples"]:
                print(f"  DROP [{d['why']}] {d.get('label')!r} seg {d.get('segment')} \"{d.get('anchor')}\"")

        asins = {"sony-xm5": "B09XS7JWHH", "switch2": "B0F3GWXLTS", "honeywell-heater": "B00VV156LA"}
        summary, per_product = {}, {}
        for k, rs in report.items():
            n0, t0 = len(LLM_LOG), time.monotonic()
            forums = [load(f.name) for f in sorted(OUT.glob(f"{k}_forums_*.json"))]
            summary[k] = out = summarize_receipts(DEMO_PRODUCTS[k], rs, forums, amazon_row(asins[k]), transcripts,
                                                  listing_title=HEATER_TITLE if k == "honeywell-heater" else None)
            calls = LLM_LOG[n0:]
            per_product[k] = (time.monotonic() - t0, sum(c["tokens"] for c in calls), sum(not c["cached"] for c in calls),
                              len(calls))
            for n, c in enumerate(calls):
                (raw_dir / f"{c['engine']}_{c['model'].split('/')[-1]}_{k}_{n}.json").write_text(json.dumps(c["reply"], indent=1))
            (raw_dir / f"summary_{k}_{VERIFY_MODEL.split('/')[-1]}.json").write_text(json.dumps(out, indent=1))
            a = out["audit"]
            print(f"\n##### {k}: status {out['status']} {out.get('why') or ''} | videos confirmed {a['confirmed_videos']}"
                  f"/{a['videos']} | threads kept {a['threads_kept']}/{a['threads']} | mismatch {out['mismatch']}")
            print(f"  threads rejected: {[(r['title'][:40], r['why']) for r in a['threads_rejected']]}")
            for lbl in ("known_issues", "known_praise"):
                for c in out[lbl]:
                    srcs = "; ".join(f"{s['channel']} {mmss(s['timestamp_s'])}" if s["type"] == "video"
                                     else f"forum: {s['title'][:45]!r} ({s['source']})" for s in c["sources"])
                    mk = ", ".join(f"Amazon '{m['title']}' ({m['sentiment']})" for m in c["marketplace"])
                    print(f"  {lbl[6:].upper():6} {c['label']!r:32} {c['source_count']} src | {srcs}" + (f" | + {mk}" if mk else ""))
                    for p in c["left_group"]:
                        print(f"         left: " + (f"{p['channel']} {mmss(p['segment_start_s'])} \"{p['quote']}\"" if p.get("video_id")
                                                   else f"forum {p.get('title')!r}") + f" ({p['why']})")
            for c in out["forum_reported"]:
                print(f"  FORUMS (reported by owners) {c['label']!r}: {c['thread_count']} threads, {c['sites']} sites | "
                      + "; ".join(f"{s['title'][:45]!r} ({s['source']}, {s['date']})" for s in c["sources"]))
                for p in c["left_group"]:
                    print(f"         left: {p.get('title') or p.get('channel')!r} ({p['why']})")
            for c in out["forum_only"]:
                print(f"  forum-only, not known {c['label']!r}: " + "; ".join(f"{s['title'][:50]!r} ({s['source']})"
                                                                           for s in c["sources"])
                      + "".join(f" | left: {p.get('title')!r} ({p['why']})" for p in c["left_group"]))
            lost = [c for c in out["mentioned_once"] if c["left_group"] or len({s.get('video_id') for s in c['sources']}) > 1]
            for c in lost:
                print(f"  NOT KNOWN {c['kind']} {c['label']!r}: " + "; ".join(
                    f"{s['channel']} {mmss(s['timestamp_s'])} ({s['model']})" for s in c["sources"] if s["type"] == "video")
                      + "".join(f" | left: " + (f"{p['channel']} {mmss(p['segment_start_s'])}" if p.get("video_id")
                                                  else f"forum {p.get('title')!r}") + f" ({p['why']})" for p in c["left_group"]))
            print("  once: " + "; ".join(f"{c['kind'][0]} {c['label']} ({c['sources'][0].get('channel')} "
                                          f"{mmss(c['sources'][0]['timestamp_s'])})" for c in out["mentioned_once"] if c not in lost))
            if a["forum_dropped"] or a["market_dropped"]:
                print(f"  mapping dropped: {a['forum_dropped'] + a['market_dropped']}")

        # Run 1's false cases: each must be gone. Collected, so one failure doesn't hide the rest.
        checks = []
        check = lambda name, ok, detail="", limit=False: checks.append((name, ok, detail, limit))
        hm = {r["video_id"]: r["model"] for r in report["honeywell-heater"]}
        sheryl_pts = [p for r in report["honeywell-heater"] if r["video_id"] == "1gmhM-sogL4" for p in r["points"]]
        check("Sheryl (HHF370) not confirmed or rejected: excluded from known by rule",
              hm["1gmhM-sogL4"]["model"] in ("not confirmed", "rejected"), f"{hm['1gmhM-sogL4']['model']}, "
              f"{len(sheryl_pts)} points", limit=True)
        for vid in ("5bQoHEQ59Vk", "qBAFwTnaLI4"):
            check(f"{ch[vid]} not confirmed (not rejected)", hm[vid]["model"] == "not confirmed", hm[vid]["why"])
        in_known = lambda k, vid: [c["label"] for lbl in ("known_issues", "known_praise") for c in summary[k][lbl]
                                   for s in c["sources"] if s.get("video_id") == vid]
        check("Sheryl in no known group", not in_known("honeywell-heater", "1gmhM-sogL4"), in_known("honeywell-heater", "1gmhM-sogL4"))
        ltt_ads = sponsor_segments(transcripts["58XieqW6CO4"])
        check("LTT dbrand reads give no points", not [p for r in report["switch2"] for p in r["points"]
                                                     if p["video_id"] == "58XieqW6CO4" and p["segment"] in ltt_ads])
        seg_at = lambda vid, t: next(i for i, s in enumerate(transcripts[vid]["transcript"]) if s["start_time_text"] == t)
        ptx = lambda vid, t: {"video_id": vid, "segment": seg_at(vid, t)}
        cases = [("Honeywell 360 (HHF360V)", "praise", "quiet operation", ptx("qBAFwTnaLI4", "7:12"), False),
                 ("Sony WH-1000XM5", "issue", "no on-ear detection cutout", ptx("6CsJZxfZsL0", "1:22"), False),
                 ("Nintendo Switch 2", "issue", "LCD screen, not OLED", ptx("58XieqW6CO4", "10:48"), False),
                 ("Sony WH-1000XM5", "issue", "doesn't fold", ptx("6CsJZxfZsL0", "7:11"), False),
                 ("Sony WH-1000XM5", "issue", "doesn't fold", ptx("6CsJZxfZsL0", "2:15"), True),
                 ("Nintendo Switch 2", "issue", "LCD screen, not OLED", ptx("Qviwd67iz5s", "8:20"), True)]
        for name, kind, label, p, want in cases:  # each as a one-point group through the batched verifier
            ok, why = verify_group(name, {"kind": kind, "label": label, "points": [p]}, transcripts)["v0"]
            check(f"verifier {ch[p['video_id']]} {transcripts[p['video_id']]['transcript'][p['segment']]['start_time_text']} "
                  f"{label!r} -> {want}", ok == want and not why.startswith("verifier failed"), why,
                  limit=(p["video_id"], label) == ("6CsJZxfZsL0", "doesn't fold") and not want)
        heat = next(t for t in load("sony-xm5_forums_problems.json")["organic_results"] if t["title"].startswith("Overheating"))
        ok, why = verify_group("Sony WH-1000XM5", {"kind": "issue", "label": "ears get warm", "points": [],
                                                   "forums": [{**_forum_src(heat), "_thread": heat}]}, {})["t0"]
        check("verifier: 'Overheating Issues' thread does NOT support 'ears get warm'",
              not ok and not why.startswith("verifier failed"), why)
        rests = [c["label"] for c in summary["sony-xm5"]["known_issues"]
                 if any((s.get("title") or "").startswith("Overheating") for s in c["sources"])]
        check("no known Sony issue rests on the 'Overheating Issues' thread", not rests, rests)
        for k, vid, t in [("honeywell-heater", "qBAFwTnaLI4", "7:12"), ("sony-xm5", "6CsJZxfZsL0", "1:22"),
                          ("switch2", "58XieqW6CO4", "10:48"), ("sony-xm5", "6CsJZxfZsL0", "7:11")]:
            bad = [c["label"] for lbl in ("known_issues", "known_praise") for c in summary[k][lbl]
                   for s in c["sources"] if s.get("video_id") == vid and s["timestamp_s"] // 1 ==
                   transcripts[vid]["transcript"][seg_at(vid, t)]["start_ms"] // 1000]
            check(f"{ch[vid]} {t} in no known group", not bad, bad, limit=(vid, t) == ("6CsJZxfZsL0", "7:11"))
        lbls = lambda k, key="known_issues": {c["label"]: c for c in summary[k][key]}
        at = lambda c, vid, t: any(s.get("video_id") == vid and s["timestamp_s"] == t for s in c["sources"])
        mk_batt = [(key, c["label"]) for key in ("known_issues", "known_praise", "mentioned_once")
                   for c in summary["sony-xm5"][key] if at(c, "6CsJZxfZsL0", 377)]
        check("Sony MKBHD 6:17 '30 h battery' not a known issue", not [x for x in mk_batt if x[0] == "known_issues"], mk_batt)
        usb = [c for c in lbls("sony-xm5").values() if "usb" in c["label"]]
        check("Sony 'no USB-C audio' known (Mike + SoundGuys)", usb and {s.get("channel") for s in usb[0]["sources"]}
              >= {"Mike O'Brien", "SoundGuys"}, [(c["label"], [s.get("channel") for s in c["sources"]]) for c in usb])
        vrr = [c["label"] for c in lbls("switch2").values() if at(c, "58XieqW6CO4", 344)]
        check("Switch 2: LTT 5:44 VRR in no known issue", not vrr, vrr)
        bait = [c["label"] for k in summary for key in ("known_issues", "forum_reported") for c in summary[k][key]
                for s in c["sources"] if "justanswer" in (s.get("link") or "")]
        check("no JustAnswer source anywhere known", not bait, bait)
        drift = [(c["label"], c["type"], c["source_count"]) for c in summary["switch2"]["known_issues"] if "drift" in c["label"]]
        check("Switch 2 drift is a known CONCERN, not a problem", drift and all(t == "concern" for _, t, _ in drift), drift)
        tr = [c["label"] for c in summary["sony-xm5"]["known_issues"] if "transparency" in c["label"]]
        check("Sony weak transparency is known", bool(tr), tr)
        hinge = [c for c in summary["sony-xm5"]["forum_reported"] if re.search(r"hinge|headband|break|broke", c["label"])]
        check("Sony hinge/headband cluster forum-reported", bool(hinge),
              [(c["label"], c["source_count"], [s["title"] for s in c["sources"]]) for c in summary["sony-xm5"]["forum_only"]])
        print("\n=== checks")
        for name, ok, detail, limit in checks:
            print(f"  {'PASS' if ok else 'LIMIT' if limit else 'FAIL'} {name}  {detail if detail else ''}")
        print("\n=== extraction time per video (sequential, Groq free tier 8k tokens/min)")
        for k, c, nseg, s, tok, live, n in timing:
            print(f"  {k:17} {c:18} {nseg:4} segs  {s:6.1f}s  {tok:6} tokens  {live}/{n} Groq calls live")
        print("=== summarize per product (merge, forums, marketplace, verifier)")
        for k, (sec, tok, live, n) in per_product.items():
            print(f"  {k:17} {sec:6.1f}s  {tok:6} tokens  {live}/{n} Groq calls live")
        print(f"  Groq tokens this run: {GROQ_TOKENS[0]}; calls {len(LLM_LOG)}, live {sum(not c['cached'] for c in LLM_LOG)}")
        assert all(ok or limit for _, ok, _, limit in checks), [n for n, ok, _, limit in checks if not (ok or limit)]

    if "--live" in sys.argv:
        from dotenv import load_dotenv
        load_dotenv()
        listing = heater_listing()
        print("\n=== live feature check (Groq), heater picks")
        for v in runs["honeywell-heater"]["picked"]:
            f = OUT / f"honeywell-heater_transcript_{v['video_id']}.json"
            got = confirm_model(heater, v, json.loads(f.read_text()) if f.exists() else None, listing)
            print(f"  {v['channel']:22} -> {got['model']}: {got['why']}" + (f"  {got.get('evidence') or got.get('llm') or ''}"))
    print("\nRECEIPTS SELF-CHECK PASSED (0 searches)")
