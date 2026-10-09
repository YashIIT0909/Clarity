"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Megaphone, ScanSearch, Store } from "lucide-react";
import { cn } from "@/lib/utils";

export type DomainClass =
  | "manufacturer"
  | "known_retailer"
  | "marketplace_listing"
  | "forum_or_scam_report"
  | "review_or_press"
  | "unknown";
export type Match = {
  domain: string;
  domain_class: DomainClass;
  url: string;
  title?: string | null;
  thumbnail?: string | null;
  price?: number | null;
  photo_distance?: number | null;
};
export type Signal = {
  signal: string;
  status: "fired" | "not_fired" | "skipped" | "unavailable";
  reason: string;
  evidence: Match[];
};
type Complaint = { source: string; url: string; title?: string | null; snippet?: string | null; matched: string };
type EngineState = { status: "running" | "done" | "unavailable"; matches?: number; ms?: number; capped?: boolean; reason?: string };

export type Forensics = {
  startedAt: number;
  lastAt: number;
  image?: string | null; // listing photo URL, or the uploaded screenshot's object URL
  imageLabel: string;
  engines: Record<string, EngineState>;
  matches: Match[];
  totalDisplay?: string;
  signals?: Signal[];
  store?: {
    domain: string;
    footprint: "none" | "thin" | "established" | null;
    complaint_hits: Complaint[] | null;
    ads: { runs_ads: boolean; advertisers: string[]; earliest_first_shown: string | null } | null;
    signals: Signal[];
  };
};

/** The SSE "forensics" events, folded into board state. */
export type ForensicsEvent = { stage: "forensics"; type: string; [k: string]: unknown };

export function reduceForensics(f: Forensics | null, e: ForensicsEvent, fallbackImage: string | null): Forensics {
  const now = Date.now();
  const base: Forensics = f ?? { startedAt: now, lastAt: now, imageLabel: "Listing photo", engines: {}, matches: [] };
  const next = { ...base, lastAt: now };
  if (e.type === "forensics_start") {
    const engines: Record<string, EngineState> = {};
    for (const name of e.engines as string[]) engines[name] = { status: "running" };
    for (const u of (e.unavailable as { engine: string; reason: string }[]) ?? [])
      engines[u.engine] = { status: "unavailable", reason: u.reason };
    return {
      ...next,
      startedAt: now,
      engines,
      image: (e.image as string | null) ?? fallbackImage,
      imageLabel: e.input === "upload" ? "Your screenshot" : "Listing photo",
    };
  }
  if (e.type === "forensics_engine_done") {
    const name = e.engine as string;
    next.engines = {
      ...next.engines,
      [name]: e.unavailable
        ? { status: "unavailable", reason: e.unavailable as string, ms: e.ms as number }
        : { status: "done", matches: e.matches_found as number, ms: e.ms as number, capped: e.capped as boolean },
    };
    return next;
  }
  if (e.type === "forensics_matches")
    return { ...next, matches: [...next.matches, ...(e.matches as Match[])], totalDisplay: e.total_display as string };
  if (e.type === "forensics_signals") return { ...next, signals: e.signals as Signal[] };
  if (e.type === "store_reputation") return { ...next, store: e as unknown as Forensics["store"] };
  return next;
}

const ENGINE_NAME: Record<string, string> = { google_lens: "Google Lens", bing_reverse_image: "Bing" };
const CLASS_META: Record<DomainClass, { label: string; dot: string; ring: string }> = {
  manufacturer: { label: "Maker's site", dot: "bg-violet-500", ring: "ring-violet-500/60" },
  known_retailer: { label: "Big retailers", dot: "bg-sky-500", ring: "ring-sky-500/60" },
  marketplace_listing: { label: "Other listings", dot: "bg-honey", ring: "ring-honey/60" },
  forum_or_scam_report: { label: "Forums & reports", dot: "bg-coral", ring: "ring-coral/60" },
  review_or_press: { label: "Reviews & press", dot: "bg-teal-500", ring: "ring-teal-500/60" },
  unknown: { label: "Other sites", dot: "bg-zinc-400", ring: "ring-zinc-400/50" },
};
const CLASS_ORDER = Object.keys(CLASS_META) as DomainClass[];
const SIGNAL_TITLE: Record<string, string> = {
  stock_photo_on_private_listing: "Maker's photo on a used listing",
  stolen_own_photo: "Same photo on another seller's listing",
  photo_on_scam_reports: "Photo appears in scam/fake reports",
  store_complaints: "Reports mention problems with this shop",
  no_web_footprint: "Shop has almost no web presence",
};

/** Nothing flagged: the board would only restate the photo rows in Trust checks. */
export const isBoardCalm = (f: Forensics) =>
  f.signals !== undefined && !f.store && !f.signals.some((s) => s.status === "fired");

export function EvidenceBoard({ f, running }: { f: Forensics; running: boolean }) {
  const engines = Object.entries(f.engines);
  const used = engines.filter(([, s]) => s.status === "done");
  const fired = [...(f.signals ?? []), ...(f.store?.signals ?? [])].filter((s) => s.status === "fired");
  const settled = f.signals !== undefined;
  const allUnavailable = settled && (f.signals ?? []).every((s) => s.status === "unavailable");

  // Engines that didn't run stay visible even when the board is calm.
  const down = engines.filter(([, s]) => s.status === "unavailable");
  const downNote = down.map(([name, s]) =>
    s.reason?.startsWith("needs a public image URL") ? `${ENGINE_NAME[name] ?? name} can't search uploaded images` : `${ENGINE_NAME[name] ?? name} unavailable`,
  );

  // The board only shouts when there's something to shout about.
  if (settled && !fired.length && !f.store) {
    return (
      <div data-testid="evidence-calm" className="mt-2 flex items-center gap-2 rounded-md bg-muted/60 px-3 py-2 text-sm text-muted-foreground">
        {allUnavailable ? <ScanSearch className="size-4 shrink-0" /> : <CheckCircle2 className="size-4 shrink-0 text-mint" />}
        {allUnavailable
          ? "Photo check unavailable this time, so it didn't count either way"
          : !f.totalDisplay
            ? `Photo searched on ${used.length} ${used.length === 1 ? "engine" : "engines"} — no other copies of it found online`
            : `Photo checked across ${used.length} ${used.length === 1 ? "engine" : "engines"} (${f.totalDisplay} matches) — nothing unusual`}
        {downNote.length > 0 && (
          <span data-testid="engines-down" title={down.map(([, s]) => s.reason).join("\n")} className="text-xs opacity-80">
            · {downNote.join(" · ")}
          </span>
        )}
      </div>
    );
  }

  return (
    <div
      data-testid="evidence-board"
      className={cn(
        "mt-2 flex min-w-0 flex-col gap-3 rounded-lg border p-3 animate-in fade-in",
        fired.length ? "border-coral/40 bg-coral/[0.03]" : "bg-muted/30",
      )}
    >
      <EngineStrip f={f} running={running && !settled} />
      {fired.map((s) => (
        <Callout key={s.signal} s={s} />
      ))}
      <div className="flex min-w-0 flex-col gap-3 sm:flex-row">
        {f.image && (
          <figure className="flex shrink-0 flex-col gap-1 sm:w-28">
            {/* eslint-disable-next-line @next/next/no-img-element -- listing/screenshot image from arbitrary hosts */}
            <img
              src={f.image}
              alt={f.imageLabel}
              className={cn(
                "aspect-square w-24 rounded-md border bg-white object-contain sm:w-28",
                fired.length && "ring-2 ring-coral",
              )}
            />
            <figcaption className="text-xs text-muted-foreground">{f.imageLabel}</figcaption>
          </figure>
        )}
        <PhotoWall matches={f.matches} />
      </div>
      {f.store && <StoreCard store={f.store} />}
    </div>
  );
}

function EngineStrip({ f, running }: { f: Forensics; running: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(id);
  }, [running]);
  const engines = Object.entries(f.engines);
  const done = engines.filter(([, s]) => s.status === "done");
  const found = f.totalDisplay ?? String(done.reduce((n, [, s]) => n + (s.matches ?? 0), 0));
  const seconds = ((running ? now : f.lastAt) - f.startedAt) / 1000;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
      {engines.map(([name, s]) => (
        <span
          key={name}
          title={s.reason}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 transition-colors duration-500",
            s.status === "running" && "text-muted-foreground",
            s.status === "done" && "border-mint/50 bg-mint/10 text-mint",
            s.status === "unavailable" && "border-dashed text-muted-foreground line-through decoration-1",
          )}
        >
          {s.status === "running" ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <span className={cn("size-1.5 rounded-full", s.status === "done" ? "bg-mint" : "bg-zinc-400")} />
          )}
          {ENGINE_NAME[name] ?? name}
          {s.status === "done" && <span className="tabular-nums opacity-70">{s.capped ? `${s.matches}+` : s.matches}</span>}
        </span>
      ))}
      <span data-testid="engine-counter" className="ml-auto tabular-nums text-muted-foreground">
        {found} matches · {done.length} {done.length === 1 ? "engine" : "engines"} · {seconds.toFixed(1)}s
      </span>
    </div>
  );
}

function PhotoWall({ matches }: { matches: Match[] }) {
  if (!matches.length) return <p className="self-center text-sm text-muted-foreground">Looking for this photo across the web…</p>;
  const groups = CLASS_ORDER.map((c) => [c, matches.filter((m) => m.domain_class === c)] as const).filter(([, ms]) => ms.length);
  return (
    <div data-testid="photo-wall" className="flex min-w-0 flex-1 flex-col gap-2">
      {groups.map(([cls, ms]) => (
        <div key={cls} className="flex min-w-0 flex-col gap-1">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className={cn("size-2 rounded-full", CLASS_META[cls].dot)} />
            {CLASS_META[cls].label} · {ms.length}
          </div>
          <div className="grid grid-cols-6 gap-1 sm:grid-cols-8">
            {ms.map((m, i) => (
              <Thumb key={m.url} m={m} delay={(i % 12) * 45} ring={CLASS_META[cls].ring} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function Thumb({ m, delay, ring, big }: { m: Match; delay?: number; ring: string; big?: boolean }) {
  const [broken, setBroken] = useState(false);
  return (
    <a
      href={m.url}
      target="_blank"
      rel="noopener noreferrer nofollow"
      title={`${m.domain}${m.title ? ` — ${m.title}` : ""}`}
      className={cn(
        "relative block aspect-square min-w-0 overflow-hidden rounded ring-1 animate-in fade-in zoom-in-75 duration-500 hover:ring-2",
        ring,
        big && "w-16 shrink-0",
      )}
      style={{ animationDelay: `${delay ?? 0}ms`, animationFillMode: "both" }}
    >
      {m.thumbnail && !broken ? (
        // eslint-disable-next-line @next/next/no-img-element -- thumbnails from arbitrary search-result hosts
        <img src={m.thumbnail} alt="" loading="lazy" onError={() => setBroken(true)} className="size-full bg-white object-cover" />
      ) : (
        <span className="flex size-full items-center justify-center bg-muted p-0.5 text-center text-[9px] leading-tight text-muted-foreground">
          {m.domain}
        </span>
      )}
    </a>
  );
}

function Callout({ s }: { s: Signal }) {
  return (
    <div data-testid="signal-callout" className="flex flex-col gap-2 rounded-md border border-coral/50 bg-coral/10 p-3 animate-in fade-in slide-in-from-top-1">
      <div className="flex items-center gap-2 text-sm font-medium text-coral">
        <AlertTriangle className="size-4 shrink-0" />
        {SIGNAL_TITLE[s.signal] ?? s.signal}
      </div>
      <p className="text-sm">{s.reason}</p>
      {s.evidence.some((e) => e.url) && (
        <div className="flex flex-wrap gap-2">
          {s.evidence
            .filter((e) => e.url)
            .slice(0, 6)
            .map((e) => (
              <div key={e.url} className="flex w-16 flex-col items-center gap-0.5">
                <Thumb m={e} ring={CLASS_META[e.domain_class]?.ring ?? CLASS_META.unknown.ring} big />
                <span className="w-full truncate text-center text-[10px] text-muted-foreground">{e.domain}</span>
                {e.photo_distance != null && <span className="text-[10px] font-medium text-coral">same image</span>}
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

const FOOTPRINT: Record<string, { label: string; tone: string }> = {
  none: { label: "No web footprint", tone: "bg-coral/10 text-coral" },
  thin: { label: "Thin web footprint", tone: "bg-honey/10 text-honey-ink" },
  established: { label: "Established site", tone: "bg-mint/10 text-mint" },
};

function StoreCard({ store }: { store: NonNullable<Forensics["store"]> }) {
  const fp = store.footprint ? FOOTPRINT[store.footprint] : null;
  return (
    <div data-testid="store-card" className="flex flex-col gap-2 rounded-md border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Store className="size-4 text-muted-foreground" />
        <span className="font-medium">{store.domain}</span>
        {fp ? <span className={cn("rounded px-1.5 py-0.5 text-xs font-medium", fp.tone)}>{fp.label}</span> : (
          <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">shop check unavailable</span>
        )}
      </div>
      {!!store.complaint_hits?.length && (
        <ul className="flex flex-col gap-1">
          {store.complaint_hits.slice(0, 4).map((h) => (
            <li key={h.url} className="text-xs text-muted-foreground">
              <a href={h.url} target="_blank" rel="noopener noreferrer nofollow" className="underline underline-offset-2">
                {h.source}
              </a>
              : {h.title ?? h.snippet}
            </li>
          ))}
        </ul>
      )}
      {store.ads && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Megaphone className="size-3.5" />
          {store.ads.runs_ads
            ? `Runs Google ads${store.ads.earliest_first_shown ? ` since ${store.ads.earliest_first_shown}` : ""} (for context only, not scored)`
            : "No Google ads found for this shop (for context only, not scored)"}
        </p>
      )}
    </div>
  );
}
