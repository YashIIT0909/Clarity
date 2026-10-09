"use client";

import { useState, type ReactNode } from "react";
import { CheckCircle2, CircleMinus, CircleSlash, Radar, Scale, ShieldCheck, Sparkles, Tag, TrendingUp, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

type Sig = { signal: string; status: "fired" | "not_fired" | "unavailable" | "skipped"; reason: string };

/** The parts of the final `done` result the charts read (trust.signals / timing.signals, unchanged from the backend). */
export type DecisionResult = {
  final_verdict: "walk_away" | "wait" | "buy_now";
  confidence: number | null;
  trust: {
    verdict: "pass" | "fail";
    signals: {
      score: number;
      pass_score: number;
      thresholds?: { min_reviews: number; min_rating: number; min_seller_sales: number; z_flag: number; sudden_drop: number };
      penalties: { check: string; weight: number; reason: string }[];
      waived: { check: string; reason: string }[];
      positives: string[];
      first_party: boolean;
      listing: {
        marketplace: "amazon" | "ebay" | "screenshot";
        price: number | null;
        reviews?: number;
        rating?: number | null;
        seller?: string;
        seller_sales?: number;
        seller_feedback?: number;
        returns_accepted?: boolean;
      };
      market: {
        median: number | null;
        sigma: number | null;
        prices: number[];
        n_prices: number;
        basis: string;
        basis_text: string;
        stores: { store: string; price: number }[];
      };
      price_ratio: number | null;
      price_z: number | null;
      price_history: { n_checks: number; since?: string; baseline?: number; drop?: number };
      seller_listings: { found: unknown[]; implausibly_cheap: unknown[] };
      photo: { signals: Sig[] };
      store: { applies: boolean; signals: Sig[] };
    };
  };
  timing: {
    verdict: "buy" | "wait";
    judged: boolean;
    evidence: { signal: string; fact: string; pulls_toward: string }[];
    signals: {
      score: number;
      components: Record<string, { score: number; weight: number; judged: boolean; [k: string]: unknown }>;
    };
  } | null;
};
type TrustSignals = DecisionResult["trust"]["signals"];
type Timing = NonNullable<DecisionResult["timing"]>;
type Demand = {
  momentum?: number;
  recent_avg?: number;
  prior_avg?: number;
  seasonal_ratio?: number | null;
  seasons_used?: number;
  seasons?: { date: string; ratio: number }[];
  series?: [string, number][];
  event?: { name: string; days_away: number; interest_change_by_year: Record<string, number>; avg_interest_change?: number } | null;
};

// Mirrors of backend constants, used only when an older result lacks `thresholds`.
const DEFAULT_THRESHOLDS = { min_reviews: 50, min_rating: 3.5, min_seller_sales: 20, z_flag: 2, sudden_drop: 0.25 };
const MAX_TIMING = 0.9; // timing confidence saturates at |score| 0.9 (0.5 + 0.9 / 2 = 95%)
const PRICE_SLOPE = 3; // timing._price: score = clip(-position * 3)
const LIFECYCLE_STEPS: [number, string][] = [[0.25, "any time"], [0.5, "within 6 months"], [0.75, "within 90 days"], [1, "within 30 days"]]; // lifecycle.score proximity

const PENALTY_LABEL: Record<string, string> = {
  too_cheap: "Price far below market",
  too_expensive: "Price far above market",
  market_unknown: "Market price unknown",
  sudden_drop: "Sudden price drop",
  no_returns: "No returns accepted",
  unproven_seller: "Unproven seller",
  seller_pattern: "Seller's other cheap listings",
  few_reviews: "Few reviews",
  low_rating: "Low rating",
  stock_photo_on_private_listing: "Stock photo on a used listing",
  stolen_own_photo: "Photo on another seller's listing",
  photo_on_scam_reports: "Photo in scam reports",
  store_complaints: "Store complaints",
  no_web_footprint: "No web footprint",
};
const SIGNAL_LABEL: Record<string, string> = {
  stock_photo_on_private_listing: "Not a stock photo on a used item",
  stolen_own_photo: "Photo not on other sellers' listings",
  photo_on_scam_reports: "Photo not in scam reports",
  store_complaints: "No store complaints",
  no_web_footprint: "Store has a web footprint",
};
const COMPONENT_LABEL: Record<string, string> = {
  demand: "Search demand",
  price: "Price position",
  maker: "Maker momentum",
  lifecycle: "Newer model",
  receipts: "Owner-reported defects",
};

const money = (n: number) =>
  `$${n.toLocaleString("en-US", { minimumFractionDigits: n < 100 ? 2 : 0, maximumFractionDigits: n < 100 ? 2 : 0 })}`;
/** Timing contributions as points (x100), one decimal so the rows add up to the net. */
const pts = (x: number) => {
  const v = Math.round(x * 1000) / 10;
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}`;
};
const pct = (x: number) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(Math.round(x * 100))}%`;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthName = (ym: string) => `${MONTHS[Number(ym.slice(5, 7)) - 1]} ${ym.slice(0, 4)}`;

/* ---------- design primitives: soft cards, dot legends, pills, tooltip cards ---------- */

type Tone = "buy" | "lime" | "wait" | "fail" | "info" | "idle";
const FILL: Record<Tone, string> = { buy: "bg-viz-buy", lime: "bg-viz-lime", wait: "bg-viz-wait", fail: "bg-viz-fail", info: "bg-viz-info", idle: "bg-viz-idle" };
const TINT: Record<Tone, string> = {
  buy: "bg-viz-buy/12",
  lime: "bg-viz-lime/20",
  wait: "bg-viz-wait/15",
  fail: "bg-viz-fail/12",
  info: "bg-viz-info/12",
  idle: "bg-viz-idle/70",
};
const STROKE: Record<Tone, string> = { buy: "stroke-viz-buy", lime: "stroke-viz-lime", wait: "stroke-viz-wait", fail: "stroke-viz-fail", info: "stroke-viz-info", idle: "stroke-viz-idle" };
const OUTLINE: Record<Tone, string> = { buy: "outline-viz-buy", lime: "outline-viz-lime", wait: "outline-viz-wait", fail: "outline-viz-fail", info: "outline-viz-info", idle: "outline-viz-idle" };
const pullTone = (x: number, dead = 0.05): Tone => (Math.abs(x) <= dead ? "idle" : x > 0 ? "buy" : "wait");

/** Status pill: tinted capsule, coloured dot, text in text tokens (never colour alone). */
function Pill({ tone, children, className }: { tone: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium text-foreground/80", TINT[tone], className)}>
      <span className={cn("size-1.5 rounded-full", FILL[tone])} />
      {children}
    </span>
  );
}

function Legend({ items }: { items: [Tone | string, string][] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {items.map(([tone, label]) => (
        <span key={label} className="inline-flex items-center gap-1.5">
          <span className={cn("size-2 rounded-full", tone in FILL ? FILL[tone as Tone] : tone)} />
          {label}
        </span>
      ))}
    </div>
  );
}

type Tip = { x: number; lines: [Tone | null, string][] } | null;

/** Hover/focus tooltip for a track: marks spread bind(xPercent, lines); a small card sits above at that x. */
function useTip() {
  const [tip, setTip] = useState<Tip>(null);
  const bind = (x: number, lines: string | [Tone | null, string][]) => {
    const l: [Tone | null, string][] = typeof lines === "string" ? [[null, lines]] : lines;
    return {
      onMouseEnter: () => setTip({ x, lines: l }),
      onMouseLeave: () => setTip(null),
      onFocus: () => setTip({ x, lines: l }),
      onBlur: () => setTip(null),
      tabIndex: 0,
      "aria-label": l.map(([, t]) => t).join(", "),
    };
  };
  const node = tip && <TipBox x={tip.x} lines={tip.lines} />;
  return { bind, node, setTip };
}

function TipBox({ x, lines }: { x: number; lines: [Tone | null, string][] }) {
  return (
    <span
      role="tooltip"
      className="pointer-events-none absolute bottom-full z-30 mb-2 flex w-max max-w-72 -translate-x-1/2 flex-col gap-1 rounded-xl border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-lg"
      style={{ left: `${Math.min(Math.max(x, 14), 86)}%` }}
    >
      {lines.map(([tone, text], i) => (
        <span key={i} className={cn("flex items-start gap-1.5", i === 0 && lines.length > 1 && "font-medium")}>
          {tone && <span className={cn("mt-1 size-2 shrink-0 rounded-full", FILL[tone])} />}
          <span>{text}</span>
        </span>
      ))}
    </span>
  );
}

function ChartCard({
  id,
  icon: Icon,
  title,
  caption,
  aside,
  children,
  className,
}: {
  id?: string;
  icon?: typeof ShieldCheck;
  title: string;
  caption?: ReactNode;
  aside?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <figure id={id} className={cn("flex min-w-0 scroll-mt-6 flex-col gap-5 rounded-3xl border border-border/60 bg-card p-5 shadow-[0_1px_3px_rgb(0_0_0/0.04)] sm:p-6", className)}>
      <figcaption className="flex flex-wrap items-start justify-between gap-3">
        <span className="flex min-w-0 items-start gap-3">
          {Icon && (
            <span className="flex size-9 shrink-0 items-center justify-center rounded-full border text-foreground/70">
              <Icon className="size-4" />
            </span>
          )}
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="text-base font-semibold tracking-tight">{title}</span>
            {caption && <span className="text-sm text-muted-foreground">{caption}</span>}
          </span>
        </span>
        {aside}
      </figcaption>
      {children}
    </figure>
  );
}

/* ---------- Verdict gauge: an arc of capsule ticks ---------- */

const CX = 100, CY = 98;
const TICKS = 25;

function TickDial({ t, from, tone, value, left, right, label }: { t: number; from: number; tone: (u: number) => Tone; value: string; left: string; right: string; label: string }) {
  const at = Math.round(Math.min(Math.max(t, 0), 1) * (TICKS - 1));
  const start = Math.round(from * (TICKS - 1));
  return (
    <svg viewBox="0 0 200 122" className="w-44 shrink-0" role="img" aria-label={label}>
      {Array.from({ length: TICKS }, (_, i) => {
        const u = i / (TICKS - 1);
        const a = Math.PI * (1 - u);
        const on = i === at || (i >= Math.min(start, at) && i <= Math.max(start, at));
        const [r0, r1] = i === at ? [56, 86] : [62, 80];
        return (
          <line
            key={i}
            x1={CX + r0 * Math.cos(a)}
            y1={CY - r0 * Math.sin(a)}
            x2={CX + r1 * Math.cos(a)}
            y2={CY - r1 * Math.sin(a)}
            strokeWidth={i === at ? 8 : 6}
            strokeLinecap="round"
            className={on ? STROKE[tone(u)] : "stroke-foreground/10"}
          />
        );
      })}
      <text x={CX} y={CY - 6} textAnchor="middle" className="fill-foreground text-[24px] font-semibold tabular-nums">{value}</text>
      <text x={14} y={CY + 18} className="fill-muted-foreground text-[11px]">{left}</text>
      <text x={186} y={CY + 18} textAnchor="end" className="fill-muted-foreground text-[11px]">{right}</text>
      <title>{label}</title>
    </svg>
  );
}

/** Walk away: the trust score against the pass mark. Buy/wait: where the timing score sits between them. */
export function VerdictGauge({ r }: { r: DecisionResult }) {
  if (r.final_verdict === "walk_away") {
    const { score, pass_score } = r.trust.signals;
    return (
      <TickDial
        t={score / 100}
        from={0}
        tone={() => (score >= pass_score ? "buy" : "fail")}
        value={`${score}`}
        left="0"
        right="100"
        label={`Trust score ${score} of 100; ${pass_score} needed to pass`}
      />
    );
  }
  if (!r.timing?.judged) return null;
  const s = r.timing.signals.score;
  const conf = Math.round((r.confidence ?? 0) * 100);
  return (
    <TickDial
      t={0.5 + Math.max(-MAX_TIMING, Math.min(MAX_TIMING, s)) / (2 * MAX_TIMING)}
      from={0.5}
      tone={(u) => (u >= 0.5 ? "buy" : "wait")}
      value={`${conf}%`}
      left="Wait"
      right="Buy"
      label={`Timing score ${pts(s)} on a scale from wait to buy; ${conf}% confidence`}
    />
  );
}

/* ---------- 1. Trust checks: a dot-matrix score + every check as a row with a status pill ---------- */

type CheckRow = { key: string; label: string; status: "fail" | "pass" | "waived" | "na"; value?: string; rule?: string; points?: number; why: string };

function trustRows(s: TrustSignals): CheckRow[] {
  const th = s.thresholds ?? DEFAULT_THRESHOLDS;
  const pen = new Map(s.penalties.map((p) => [p.check, p]));
  const waived = new Map(s.waived.map((w) => [w.check, w]));
  const L = s.listing;
  const rows: CheckRow[] = [];
  const add = (key: string, label: string, ok: boolean | null, value: string | undefined, rule: string, why: string, failLabel?: string) => {
    const p = pen.get(key), w = waived.get(key);
    rows.push({
      key,
      label: p && failLabel ? failLabel : label,
      status: p ? "fail" : w ? "waived" : ok === null ? "na" : ok ? "pass" : "fail",
      value,
      rule,
      points: p?.weight,
      why: p?.reason ?? w?.reason ?? why,
    });
  };

  // Price vs market: one row, whichever of the three price checks applied.
  const priceKey = ["too_cheap", "too_expensive", "market_unknown"].find((k) => pen.has(k) || waived.has(k)) ?? "too_cheap";
  const r = s.price_ratio;
  add(
    priceKey,
    "Price close to market",
    r == null ? null : true,
    r == null ? undefined : Math.abs(r - 1) <= 0.02 ? "at median" : `${pct(r - 1)} vs median`,
    `within ${th.z_flag}σ of the median`,
    r == null ? "No market price to compare against — not counted." : "Inside the normal price range.",
    PENALTY_LABEL[priceKey],
  );

  const h = s.price_history;
  add(
    "sudden_drop",
    "No sudden price drop",
    h.n_checks ? true : null,
    h.n_checks && h.drop != null ? `${pct(-h.drop)} vs usual` : undefined,
    `under ${Math.round(th.sudden_drop * 100)}% below its usual price`,
    h.n_checks ? `Usually ${money(h.baseline ?? 0)} across ${h.n_checks} earlier checks since ${h.since}.` : "No earlier checks of this listing to compare.",
    PENALTY_LABEL.sudden_drop,
  );

  if (L.marketplace === "amazon") {
    add("few_reviews", "Enough reviews", true, (L.reviews ?? 0).toLocaleString("en-US"), `at least ${th.min_reviews}`, `${(L.reviews ?? 0).toLocaleString("en-US")} reviews on this listing.`, PENALTY_LABEL.few_reviews);
    add("low_rating", "Rating", L.rating == null ? null : true, L.rating == null ? undefined : `${L.rating}/5`, `at least ${th.min_rating}/5`, L.rating == null ? "No rating shown." : `Rated ${L.rating}/5.`, PENALTY_LABEL.low_rating);
  }
  if (L.marketplace === "ebay") {
    add("unproven_seller", "Proven seller", true, `${(L.seller_sales ?? 0).toLocaleString("en-US")} sales`, `at least ${th.min_seller_sales} sales`, `Seller ${L.seller} has ${L.seller_sales} sales.`, PENALTY_LABEL.unproven_seller);
    add("no_returns", "Returns accepted", true, L.returns_accepted ? "yes" : "no", "", L.returns_accepted ? "The seller accepts returns." : "The seller accepts no returns.", PENALTY_LABEL.no_returns);
    const found = s.seller_listings.found.length;
    add(
      "seller_pattern",
      "Seller's other listings normal",
      s.market.median ? true : null,
      !s.market.median ? undefined : found ? `${s.seller_listings.implausibly_cheap.length} of ${found} too cheap` : "none found",
      "none implausibly cheap",
      found ? `${found} other listing(s) of this product by the seller.` : "No other listings of this product by the seller.",
      PENALTY_LABEL.seller_pattern,
    );
  }

  for (const sig of [...s.photo.signals, ...(s.store.applies ? s.store.signals : [])])
    add(
      sig.signal,
      SIGNAL_LABEL[sig.signal] ?? sig.signal,
      sig.status === "fired" ? false : sig.status === "not_fired" ? true : null,
      undefined,
      "",
      sig.reason,
      PENALTY_LABEL[sig.signal],
    );

  const order = { fail: 0, pass: 1, waived: 2, na: 3 };
  return rows.sort((a, b) => order[a.status] - order[b.status]);
}

const STATUS: Record<CheckRow["status"], { icon: typeof CheckCircle2; tone: Tone; text: string; cls: string }> = {
  fail: { icon: XCircle, tone: "fail", text: "Failed", cls: "text-viz-fail" },
  pass: { icon: CheckCircle2, tone: "buy", text: "Passed", cls: "text-viz-buy" },
  waived: { icon: CircleSlash, tone: "idle", text: "Not counted", cls: "text-muted-foreground" },
  na: { icon: CircleMinus, tone: "idle", text: "Couldn't check", cls: "text-muted-foreground/70" },
};

export function TrustChecks({ s }: { s: TrustSignals }) {
  const [open, setOpen] = useState<string | null>(null);
  const rows = trustRows(s);
  const pass = s.score >= s.pass_score;
  const counts = { pass: rows.filter((r) => r.status === "pass").length, fail: rows.filter((r) => r.status === "fail").length };

  return (
    <ChartCard
      id="trust-checks"
      icon={ShieldCheck}
      title="Trust checks"
      caption={`Score ${s.score}/100 · ${counts.fail ? `${counts.fail} failed, ` : ""}${counts.pass} passed${pass ? "" : ` · needs ${s.pass_score} to pass`}`}
    >
      <ul className="flex flex-col">
        {rows.map((r) => {
          const st = STATUS[r.status];
          const Icon = st.icon;
          return (
            <li key={r.key} className="border-t border-border/60 first:border-t-0">
              <button
                type="button"
                aria-expanded={open === r.key}
                onClick={() => setOpen(open === r.key ? null : r.key)}
                className={cn(
                  "-mx-2 grid w-[calc(100%+1rem)] grid-cols-[1rem_1fr_auto] items-center gap-x-3 rounded-xl px-2 py-2.5 text-left text-sm outline-none hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring",
                )}
              >
                <Icon className={cn("size-4", st.cls)} aria-hidden />
                <span className={cn("min-w-0", r.status === "na" && "text-muted-foreground")}>
                  <span className="block">{r.label}</span>
                  {r.rule && <span className="block text-xs text-muted-foreground">{r.rule}</span>}
                </span>
                <span className="flex items-center gap-2.5">
                  {r.value && <span className="text-sm font-medium tabular-nums">{r.value}</span>}
                  {r.status !== "pass" && <Pill tone={st.tone}>{r.points != null ? `−${r.points}` : st.text}</Pill>}
                </span>
              </button>
              {open === r.key && <p className="pb-2.5 pl-7 text-xs text-muted-foreground animate-in fade-in">{r.why}</p>}
            </li>
          );
        })}
      </ul>
    </ChartCard>
  );
}

/* ---------- 2. Price vs market: a dot-matrix histogram of comparable prices ---------- */

const BINS = 20;

export function PriceBand({ s }: { s: TrustSignals }) {
  const { bind, node } = useTip();
  const { median, sigma, prices, basis_text, stores } = s.market;
  const price = s.listing.price;
  const zf = (s.thresholds ?? DEFAULT_THRESHOLDS).z_flag;

  if (!median || !sigma || !prices.length)
    return (
      <ChartCard id="price" icon={Tag} title="Price vs the market" caption={price == null ? "No price to check for this listing." : "Not enough comparable prices to place this one."}>
        <p className="text-sm text-muted-foreground">Nothing to plot — this check didn’t count either way.</p>
      </ChartCard>
    );

  const bandLo = Math.max(0, median - zf * sigma), bandHi = median + zf * sigma;
  const values = [...prices, bandLo, bandHi, ...(price != null ? [price] : [])];
  const span = Math.max(...values) - Math.min(...values) || 1;
  const lo = Math.max(0, Math.min(...values) - span * 0.04), hi = Math.max(...values) + span * 0.04;
  const x = (v: number) => ((v - lo) / (hi - lo)) * 100;
  const bin = (v: number) => Math.min(BINS - 1, Math.max(0, Math.floor(((v - lo) / (hi - lo)) * BINS)));
  const cols: number[][] = Array.from({ length: BINS }, () => []);
  for (const v of [...prices].sort((a, b) => a - b)) cols[bin(v)].push(v);
  const mine = price != null ? bin(price) : -1;
  const tall = Math.max(...cols.map((c, i) => c.length + (i === mine ? 1 : 0)), 3);
  const storeAt = (v: number) => stores.find((st) => st.price === v)?.store;
  const inBand = (v: number) => v >= bandLo && v <= bandHi;

  const z = s.price_z;
  const flagged = z != null && Math.abs(z) > zf;
  const meTone: Tone = z == null ? "info" : z < -zf ? "fail" : z > zf ? "wait" : "buy";
  const ratio = s.price_ratio;
  const rel = ratio == null ? "" : Math.abs(ratio - 1) <= 0.02 ? "at" : `${Math.abs(Math.round((ratio - 1) * 100))}% ${ratio > 1 ? "above" : "below"}`;

  return (
    <ChartCard
      id="price"
      icon={Tag}
      title="Price vs the market"
      caption={`${prices.length} comparable prices (${basis_text})`}
    >
      <div className="flex flex-col">
        <span className="text-4xl font-semibold tracking-tight tabular-nums">{price != null ? money(price) : "—"}</span>
        {price != null && ratio != null && (
          <span className="text-sm text-muted-foreground">
            {rel} the median of {money(median)}
            {flagged ? " · outside the normal range, so it was flagged" : " · inside the normal range"}
          </span>
        )}
      </div>
      <Legend items={[[meTone, "this listing"], ["lime", "within the normal range"], ...(prices.some((v) => !inBand(v)) ? [["idle", "outside it"] as [Tone, string]] : [])]} />
      <div className="relative">
        {node}
        <div className="relative" style={{ height: tall * 19 + 6 }}>
          <div className="absolute inset-0">
            {/* normal range: within z_flag sigma of the median is never flagged */}
            <span className="absolute inset-y-0 rounded-2xl bg-viz-lime/10" style={{ left: `${x(bandLo)}%`, width: `${x(bandHi) - x(bandLo)}%` }} />
            <span className="absolute inset-y-0 border-l-2 border-dashed border-foreground/30" style={{ left: `${x(median)}%` }} />
            <div className="absolute inset-0 grid items-end gap-[3px]" style={{ gridTemplateColumns: `repeat(${BINS}, minmax(0, 1fr))` }}>
              {cols.map((c, i) => (
                <div key={i} className="relative flex h-full flex-col-reverse items-center gap-[3px]">
                  {i === mine && <span aria-hidden className="absolute inset-y-0 left-1/2 border-l-2 border-dashed border-foreground/50" />}
                  {c.map((v, j) => (
                    <span
                      key={j}
                      {...bind(((i + 0.5) / BINS) * 100, [[inBand(v) ? "lime" : "idle", `${money(v)}${storeAt(v) ? ` · ${storeAt(v)}` : ""}`]])}
                      className={cn("relative aspect-square w-full max-w-4 rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring", inBand(v) ? "bg-viz-lime" : "bg-viz-idle")}
                    />
                  ))}
                  {i === mine && (
                    <span
                      {...bind(((i + 0.5) / BINS) * 100, [[meTone, `This listing: ${money(price!)}`], [null, flagged ? "Outside the normal range — flagged" : "Inside the normal range"]])}
                      className={cn("relative aspect-square w-full max-w-4 rounded-full outline-2 outline-offset-2", FILL[meTone], OUTLINE[meTone])}
                    />
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="relative mt-2 h-4 text-xs tabular-nums text-muted-foreground">
          <span className="absolute left-0">{money(lo)}</span>
          <span className="absolute -translate-x-1/2" style={{ left: `${x(median)}%` }}>median</span>
          <span className="absolute right-0">{money(hi)}</span>
        </div>
      </div>
    </ChartCard>
  );
}

/* ---------- 3. Timing drivers (capsule bars) + what would flip it ---------- */

/** The price at which the timing verdict flips, holding every other signal fixed (timing._price, PRICE_SLOPE). */
function flipPrice(t: Timing, s: TrustSignals) {
  const c = t.signals.components.price, median = s.market.median, price = s.listing.price;
  if (!c?.judged || !median || price == null) return null;
  const net = t.signals.score;
  const needed = (c.score * c.weight - net) / c.weight; // the price score that puts the total exactly on the line
  if (Math.abs(needed) > 1) return { price: null, contribution: null };
  return { price: median * (1 - needed / PRICE_SLOPE), contribution: needed * c.weight };
}

function flipNotes(t: Timing, s: TrustSignals): string[] {
  const net = t.signals.score, buy = net > 0, comps = t.signals.components;
  const notes: string[] = [];
  const f = flipPrice(t, s);
  if (f?.price != null)
    notes.push(buy ? `the price rises to ${money(f.price)} or more (now ${money(s.listing.price!)})` : `the price drops below ${money(f.price)} (now ${money(s.listing.price!)})`);
  else if (f) notes.push(buy ? "no price change alone could flip it" : "no price drop alone could flip it");
  if (buy) {
    const lc = comps.lifecycle;
    if (lc && lc.score === 0) {
      const step = LIFECYCLE_STEPS.find(([p]) => p * lc.weight >= net);
      if (step) notes.push(`a newer model is announced for release ${step[1]}`);
    }
    const rc = comps.receipts;
    if (rc && rc.score === 0 && rc.weight >= net) notes.push("owners and reviewers confirm a recurring defect");
    else if (rc && rc.score === 0 && rc.weight / 2 >= net) notes.push("reviewers confirm a likely risk");
  }
  return notes;
}

const ROW = "grid grid-cols-[minmax(0,7.5rem)_1fr_auto] items-center gap-3 text-sm sm:grid-cols-[minmax(0,9.5rem)_1fr_auto]";

export function TimingDrivers({ t, s, wide = true }: { t: Timing; s: TrustSignals; wide?: boolean }) {
  const { bind, node } = useTip();
  const comps = Object.entries(t.signals.components);
  const max = Math.max(...comps.map(([, c]) => c.weight)); // the most any one signal can move the score
  const facts = (k: string) => t.evidence.filter((e) => e.signal === k).map((e) => e.fact);
  const net = t.signals.score;
  const buy = net > 0;
  const flip = flipPrice(t, s);
  const notes = flipNotes(t, s);
  const half = (v: number) => (Math.min(Math.abs(v), max) / max) * 50;
  const at = (v: number) => 50 + (v >= 0 ? half(v) : -half(v));

  return (
    <ChartCard
      id="timing"
      icon={Scale}
      title="What drives the timing"
      caption="How hard each signal pushes. Hover a bar for the facts."
      className={wide ? "md:col-span-2" : undefined}
      aside={<Legend items={[["wait", "wait"], ["buy", "buy now"]]} />}
    >
      <div className="relative flex flex-col gap-3.5">
        {node}
        {comps.map(([k, c]) => {
          const v = c.score * c.weight;
          const why = facts(k);
          const lines: [Tone | null, string][] = [[null, COMPONENT_LABEL[k] ?? k], ...why.map((f): [Tone | null, string] => [pullTone(v, 0), f])];
          const ghost = k === "price" && flip?.contribution != null && flip.price != null ? flip.contribution : null;
          return (
            <div key={k} className={ROW}>
              <span className="min-w-0 leading-tight">
                <span className="block">{COMPONENT_LABEL[k] ?? k}</span>
                <span className="block text-xs text-muted-foreground">max {Math.round(c.weight * 100)}</span>
              </span>
              <div className="relative h-3.5 rounded-full bg-viz-idle/70">
                <span aria-hidden className="absolute inset-y-[-5px] left-1/2 border-l-2 border-dashed border-foreground/25" />
                {c.judged && v !== 0 ? (
                  <span
                    {...bind(at(v / 2), lines)}
                    className={cn("absolute inset-y-0 rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring", v > 0 ? "bg-viz-buy" : "bg-viz-wait")}
                    style={v > 0 ? { left: "50%", width: `${half(v)}%` } : { right: "50%", width: `${half(v)}%` }}
                  />
                ) : (
                  <span {...bind(50, why.length ? lines : [[null, "No data for this signal."]])} className="absolute inset-0 outline-none focus-visible:ring-2 focus-visible:ring-ring" />
                )}
                {ghost != null && (
                  <span
                    {...bind(at(ghost), [[buy ? "wait" : "buy", `At ${money(flip!.price!)} the verdict flips to ${buy ? "wait" : "buy now"}`]])}
                    className="absolute inset-y-[-4px] z-10 w-0 border-l-2 border-dashed border-foreground/70 outline-none"
                    style={{ left: `${at(ghost)}%` }}
                  />
                )}
              </div>
              {c.judged && v !== 0 ? (
                <Pill tone={pullTone(v, 0)} className="min-w-16 justify-center tabular-nums">{pts(v)}</Pill>
              ) : (
                <Pill tone="idle" className="min-w-16 justify-center">{c.judged || k === "lifecycle" || k === "receipts" ? "0" : "no data"}</Pill>
              )}
            </div>
          );
        })}
        <div className={cn(ROW, "border-t border-border/60 pt-4")}>
          <span className="font-medium">Net score</span>
          <div className="relative h-5 rounded-full bg-viz-idle/70">
            <span aria-hidden className="absolute inset-y-[-5px] left-1/2 border-l-2 border-foreground/40" />
            <span
              className={cn("absolute inset-y-0 rounded-full", buy ? "bg-viz-buy" : "bg-viz-wait")}
              style={buy ? { left: "50%", width: `${(Math.min(net, MAX_TIMING) / MAX_TIMING) * 50}%` } : { right: "50%", width: `${(Math.min(-net, MAX_TIMING) / MAX_TIMING) * 50}%` }}
            />
          </div>
          <Pill tone={buy ? "buy" : "wait"} className="min-w-16 justify-center tabular-nums">{pts(net)}</Pill>
        </div>
        {notes.length > 0 && (
          <div className="flex gap-3 rounded-2xl bg-viz-lime/15 px-4 py-3 text-sm">
            <Sparkles className="mt-0.5 size-4 shrink-0 text-foreground/60" />
            <span>
              <span className="font-medium">Flips to {buy ? "wait" : "buy now"} if</span>{" "}
              <span className="text-muted-foreground">
                {notes.join("; or ")}.{flip?.price != null && " Dashed mark on the price bar."}
              </span>
            </span>
          </div>
        )}
      </div>
    </ChartCard>
  );
}

/* ---------- 4. Search interest: dot-matrix columns by month + KPI tiles with capsule sparklines ---------- */

/** Capsule sparkline: thin rounded bars, heights 0..1. */
function Capsules({ bars, className }: { bars: { h: number; tone: Tone; tip: string }[]; className?: string }) {
  const { bind, node } = useTip();
  return (
    <div className={cn("relative flex h-12 items-end gap-1", className)}>
      {node}
      {bars.map((b, i) => (
        <span
          key={i}
          {...bind(((i + 0.5) / bars.length) * 100, [[b.tone, b.tip]])}
          className={cn("w-1.5 rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring", FILL[b.tone])}
          style={{ height: `${Math.max(b.h, 0.08) * 100}%` }}
        />
      ))}
    </div>
  );
}

function Kpi({ label, value, sub, pill, children }: { label: string; value: string; sub: string; pill?: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-2 rounded-2xl border border-border/60 p-4">
      <span className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        {label}
        {pill}
      </span>
      <div className="flex items-end justify-between gap-3">
        <span className="flex min-w-0 flex-col">
          <span className="text-3xl font-semibold tracking-tight tabular-nums">{value}</span>
          <span className="text-xs text-muted-foreground">{sub}</span>
        </span>
        {children}
      </div>
    </div>
  );
}

const DOT_ROWS = 10; // each dot = 10 points of search interest

export function SearchInterest({ t }: { t: Timing }) {
  const d = t.signals.components.demand as Demand & { judged: boolean };
  const [hover, setHover] = useState<number | null>(null);
  const series = d.series ?? [];
  const ev = d.event;
  const years = Object.entries(ev?.interest_change_by_year ?? {});

  if (!d.judged || series.length < 12)
    return (
      <ChartCard
        id="demand"
        icon={TrendingUp}
        title="Search interest"
        className="md:col-span-2"
        caption={d.judged ? "This result predates the chart data. Run the product again to see it." : "Not enough search-interest data for this product, so demand wasn’t judged."}
      />
    );

  const n = series.length;
  const pull = (x: number) => pullTone(x);
  // Weeks Clarity compared: the last 4, the 8 before, and each past year's "next 8 weeks".
  const weekIdx = new Map(series.map(([day], i) => [day, i]));
  const seasonWeeks = new Set((d.seasons ?? []).flatMap((s) => {
    const i = weekIdx.get(s.date);
    return i == null ? [] : Array.from({ length: 9 }, (_, k) => i + k);
  }));
  const months: { ym: string; avg: number; recent: boolean; prior: boolean; season: boolean }[] = [];
  series.forEach(([day, v], i) => {
    const ym = day.slice(0, 7);
    let m = months.at(-1);
    if (!m || m.ym !== ym) months.push((m = { ym, avg: 0, recent: false, prior: false, season: false }));
    m.avg += v;
    (m as { n?: number }).n = ((m as { n?: number }).n ?? 0) + 1;
    if (i >= n - 4) m.recent = true;
    else if (i >= n - 12) m.prior = true;
    if (seasonWeeks.has(i)) m.season = true;
  });
  for (const m of months) m.avg /= (m as { n?: number }).n ?? 1;
  const toneOf = (m: (typeof months)[number]): string =>
    m.season ? "bg-viz-buy" : "bg-viz-lime";
  const momentum = d.momentum ?? 0, seasonal = d.seasonal_ratio;
  const last12 = series.slice(-12);
  const top12 = Math.max(...last12.map(([, v]) => v), 1);
  const seasonMax = Math.max(...(d.seasons ?? []).map((s) => s.ratio), 1);
  const yearMax = Math.max(...years.map(([, v]) => Math.abs(v)), 0.1);

  return (
    <ChartCard
      id="demand"
      icon={TrendingUp}
      title="Search interest"
      className="md:col-span-2"
      caption="Google searches for this product, by month, 5 years · each dot = 10 points of interest (100 = peak)"
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <Kpi label="Last 4 weeks vs the 8 before" value={pct(momentum)} sub={`${Math.round(d.recent_avg ?? 0)} vs ${Math.round(d.prior_avg ?? 0)} interest`}
          pill={pull(momentum) !== "idle" && <Pill tone={pull(momentum)}>{pull(momentum) === "buy" ? "buy now" : "wait"}</Pill>}>
          <Capsules bars={last12.map(([day, v], i) => ({ h: v / top12, tone: i >= 8 ? "buy" : "lime", tip: `Week of ${day}: ${v}` }))} />
        </Kpi>
        {seasonal != null && (
          <Kpi label="Next 8 weeks, in past years" value={pct(seasonal - 1)} sub={`average of ${d.seasons_used} earlier year${d.seasons_used === 1 ? "" : "s"}`}
            pill={pull(seasonal - 1) !== "idle" && <Pill tone={pull(seasonal - 1)}>{pull(seasonal - 1) === "buy" ? "buy now" : "wait"}</Pill>}>
            <Capsules bars={(d.seasons ?? []).slice().reverse().map((s) => ({ h: s.ratio / seasonMax, tone: s.ratio >= 1 ? "buy" : "wait", tip: `From ${s.date}: ${pct(s.ratio - 1)} over 8 weeks` }))} />
          </Kpi>
        )}
        {ev && (
          <Kpi label={`Until ${ev.name}`} value={`${ev.days_away} days`} sub={years.length ? `searches ${pct(ev.avg_interest_change ?? 0)} around it, on average` : "no past data around it"}
            pill={<Pill tone="idle">context only</Pill>}>
            {years.length > 0 && <Capsules bars={years.map(([y, v]) => ({ h: Math.abs(v) / yearMax, tone: v >= 0 ? "lime" : "wait", tip: `${y}: ${pct(v)} around ${ev.name}` }))} />}
          </Kpi>
        )}
      </div>

      <div className="flex flex-col gap-3">
        {(d.seasons ?? []).length > 0 && <Legend items={[["buy", "the next 8 weeks, in past years"], ["lime", "other months"]]} />}
        <div className="relative" onMouseLeave={() => setHover(null)}>
          {hover != null && (
            <TipBox
              x={((hover + 0.5) / months.length) * 100}
              lines={[[null, monthName(months[hover].ym)], [months[hover].season ? "buy" : "lime", `interest ${Math.round(months[hover].avg)}`]]}
            />
          )}
          <div
            className="grid h-36 items-end gap-[2px] sm:h-44"
            style={{ gridTemplateColumns: `repeat(${months.length}, minmax(0, 1fr))` }}
            role="img"
            aria-label={`Monthly search interest over ${Math.round(n / 52)} years; last 4 weeks ${pct(momentum)} versus the 8 before`}
          >
            {months.map((m, i) => {
              const k = m.avg > 0 ? Math.max(1, Math.round(m.avg / (100 / DOT_ROWS))) : 0;
              return (
                <div key={m.ym} onMouseEnter={() => setHover(i)} className="relative flex h-full flex-col-reverse items-center gap-[2px]">
                  {hover === i && (
                    <span aria-hidden className="absolute inset-y-0 left-1/2 border-l-2 border-dashed border-foreground/40" />
                  )}
                  {Array.from({ length: k }, (_, j) => (
                    <span key={j} className={cn("relative aspect-square w-full max-w-3 rounded-full", toneOf(m))} />
                  ))}
                </div>
              );
            })}
          </div>
          <div className="relative mt-2 h-4 text-xs tabular-nums text-muted-foreground">
            {months.map((m, i) =>
              m.ym.endsWith("-01") ? (
                <span key={m.ym} className="absolute -translate-x-1/2" style={{ left: `${((i + 0.5) / months.length) * 100}%` }}>
                  {m.ym.slice(0, 4)}
                </span>
              ) : null,
            )}
          </div>
        </div>
      </div>
    </ChartCard>
  );
}

/* ---------- the section ---------- */

/** successor: the newer-model card, shown beside the timing chart when one was found. */
export function DecisionCharts({ r, successor }: { r: DecisionResult; successor?: ReactNode }) {
  return (
    <section className="flex flex-col gap-3" aria-label="Decision at a glance">
      <h2 className="text-xl font-semibold tracking-tight">The evidence</h2>
      <div className="grid gap-4 md:grid-cols-2">
        <TrustChecks s={r.trust.signals} />
        <PriceBand s={r.trust.signals} />
        {r.timing?.judged ? (
          <>
            <TimingDrivers t={r.timing} s={r.trust.signals} wide={!successor} />
            {successor && (
              <ChartCard id="successor" icon={Radar} title="Newer model">
                {successor}
              </ChartCard>
            )}
            <SearchInterest t={r.timing} />
          </>
        ) : null}
      </div>
    </section>
  );
}
