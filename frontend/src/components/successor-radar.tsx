"use client";

import { CheckCircle2, Loader2, Newspaper, Radar } from "lucide-react";
import { cn } from "@/lib/utils";

type EngineState = { status: "running" | "done" | "unavailable"; found?: number; ms?: number; reason?: string };
type Evidence = { source: "trends" | "autocomplete" | "news"; text: string; value?: string | null };
type Candidate = { name: string; identifier: number | string; sources: string[]; trends_value: string | null; evidence: Evidence[] };
type Headline = { title: string; source: string | null; date: string | null; link: string | null; stage: string };

export type Lifecycle = {
  engines: Record<string, EngineState>;
  candidates?: Candidate[];
  status?: { name: string | null; status: string; date: string | null; window: string | null; headlines: Headline[]; why: string | null };
};

/** The SSE "lifecycle" events (timing step), folded into card state. */
export type LifecycleEvent = { stage: "lifecycle"; type: string; [k: string]: unknown };

export function reduceLifecycle(l: Lifecycle | null, e: LifecycleEvent): Lifecycle {
  const base: Lifecycle = l ?? { engines: {} };
  if (e.type === "start")
    return { engines: Object.fromEntries((e.engines as string[]).map((n) => [n, { status: "running" } as EngineState])) };
  if (e.type === "engine_done")
    return {
      ...base,
      engines: {
        ...base.engines,
        [e.engine as string]: e.unavailable
          ? { status: "unavailable", reason: e.unavailable as string, ms: e.ms as number }
          : { status: "done", found: e.found as number, ms: e.ms as number },
      },
    };
  if (e.type === "candidates") return { ...base, candidates: e.candidates as Candidate[] };
  if (e.type === "status") return { ...base, status: e as unknown as Lifecycle["status"] };
  return base;
}

const ENGINE_NAME: Record<string, string> = {
  google_trends: "Trends",
  google_autocomplete: "Autocomplete",
  google_news: "News",
};
const TRACK = ["rumored", "announced", "released"] as const;
const day = (iso: string | null | undefined) =>
  iso ? new Date(iso.length === 10 ? `${iso}T12:00:00Z` : iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : null;

/** A newer model was found (the full card, not a one-line "none"/"unavailable"). */
export const hasSuccessor = (l: Lifecycle | null) => !!l?.status && l.status.status !== "none" && l.status.status !== "unavailable";

/** bare: drawn inside an evidence card, so no border or padding of its own. */
export function SuccessorRadar({ l, running, bare }: { l: Lifecycle; running: boolean; bare?: boolean }) {
  const s = l.status;
  const engines = Object.entries(l.engines);
  const down = engines.filter(([, e]) => e.status === "unavailable");

  // Calm lines: nothing found, or the check couldn't run (and why).
  if (s && (s.status === "none" || s.status === "unavailable")) {
    const text =
      s.status === "none"
        ? "No newer model found in searches or news"
        : !engines.length
          ? "Newer-model check didn't run: no model number in the product name"
          : `Newer-model check unavailable: ${down.map(([n]) => ENGINE_NAME[n] ?? n).join(", ") || "an engine"} didn't respond, so it didn't count either way`;
    return (
      <div data-testid="radar-calm" className="mt-2 flex items-center gap-2 rounded-md bg-muted/60 px-3 py-2 text-sm text-muted-foreground">
        {s.status === "none" ? <CheckCircle2 className="size-4 shrink-0 text-emerald-600" /> : <Radar className="size-4 shrink-0" />}
        <span className="min-w-0">{text}</span>
      </div>
    );
  }

  const top = s?.name ? l.candidates?.find((c) => c.name === s.name) ?? l.candidates?.[0] : l.candidates?.[0];
  const current = s?.status.startsWith("rumored") ? "rumored" : s?.status;
  const stale = s?.status === "rumored (stale)";
  const tone = current === "announced" ? "amber" : current === "released" ? "sky" : "zinc";
  const rising = top?.evidence.filter((e) => e.source === "trends") ?? [];
  const typed = top?.evidence.filter((e) => e.source === "autocomplete") ?? [];
  const headlines = (s?.headlines ?? []).slice(0, 3);

  return (
    <div
      data-testid="successor-radar"
      className={cn(
        "flex min-w-0 flex-col gap-3 animate-in fade-in",
        !bare && "mt-2 rounded-lg border p-3",
        !bare && (current === "announced" ? "border-amber-500/40 bg-amber-500/[0.03]" : "bg-muted/30"),
      )}
    >
      <EngineStrip engines={engines} running={running && !s} />

      {top ? (
        <div className="flex min-w-0 flex-col gap-2">
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
            {!bare && <span className="text-xs uppercase tracking-wide text-muted-foreground">Newer model</span>}
            <span data-testid="radar-model" className="min-w-0 break-words text-xl font-semibold tracking-tight">
              {top.name}
            </span>
          </div>
          {s && <StatusTrack current={current} tone={tone} date={s.date} window={s.window} stale={stale} />}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Looking for a newer model in searches and news…</p>
      )}

      {(rising.length > 0 || typed.length > 0) && (
        <div className="flex min-w-0 flex-col gap-1.5">
          {rising.length > 0 && (
            <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs">
              <span className="text-muted-foreground">Rising searches</span>
              {rising.slice(0, 4).map((e) => (
                <span key={e.text} className="inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-0.5">
                  <span className="truncate">{e.text}</span>
                  {e.value && (
                    <span className={cn("shrink-0 font-medium tabular-nums", e.value === "Breakout" ? "text-red-600 dark:text-red-400" : "text-emerald-700 dark:text-emerald-400")}>
                      {e.value}
                    </span>
                  )}
                </span>
              ))}
            </div>
          )}
          {typed.length > 0 && (
            <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs">
              <span className="text-muted-foreground">People type</span>
              {typed.slice(0, 3).map((e) => (
                <span key={e.text} className="max-w-full truncate rounded-full bg-muted px-2 py-0.5">
                  {e.text}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {headlines.length > 0 && (
        <ul data-testid="radar-headlines" className="flex min-w-0 flex-col gap-1.5">
          {headlines.map((h) => (
            <li key={`${h.link}${h.title}`} className="flex min-w-0 gap-2 text-xs">
              <Newspaper className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0">
                <a href={h.link ?? undefined} target="_blank" rel="noopener noreferrer nofollow" className="underline decoration-foreground/30 underline-offset-2 hover:decoration-foreground">
                  {h.title}
                </a>
                <span className="text-muted-foreground">
                  {" "}
                  · {h.source}
                  {h.date && ` · ${day(h.date)}`}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function StatusTrack({ current, tone, date, window, stale }: { current?: string; tone: string; date: string | null; window: string | null; stale: boolean }) {
  const at = TRACK.indexOf(current as (typeof TRACK)[number]);
  const on = {
    amber: "border-amber-500 bg-amber-500 text-white",
    sky: "border-sky-600 bg-sky-600 text-white",
    zinc: "border-zinc-500 bg-zinc-500 text-white",
  }[tone];
  return (
    <div data-testid="radar-track" className="flex min-w-0 flex-col gap-1">
      <ol className="flex min-w-0 items-center gap-1 text-xs">
        {TRACK.map((step, i) => (
          <li key={step} className="flex min-w-0 flex-1 items-center gap-1">
            <span
              className={cn(
                "shrink-0 rounded-full border px-2 py-0.5 capitalize",
                i === at ? cn(on, "font-medium") : i < at ? "border-foreground/30 text-foreground/70" : "border-dashed text-muted-foreground/60",
              )}
            >
              {step}
            </span>
            {i < TRACK.length - 1 && <span className={cn("h-px min-w-2 flex-1", i < at ? "bg-foreground/30" : "bg-foreground/10")} />}
          </li>
        ))}
      </ol>
      <p className="text-xs text-muted-foreground">
        {current === "released" && date && `Out since ${day(date)}`}
        {/* the window as the headline states it; date is then only our end-of-window reading, never shown as fact */}
        {current === "announced" && (window ? `Due ${window}` : `Announced ${day(date) ?? ""}; no release date in the headlines`)}
        {current === "rumored" && (stale ? "Only old rumors, too little to go on" : "Rumored only, nothing official yet")}
      </p>
    </div>
  );
}

function EngineStrip({ engines, running }: { engines: [string, EngineState][]; running: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
      {engines.map(([name, s]) => (
        <span
          key={name}
          title={s.reason}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 transition-colors duration-500",
            s.status === "running" && "text-muted-foreground",
            s.status === "done" && "border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
            s.status === "unavailable" && "border-dashed text-muted-foreground line-through decoration-1",
          )}
        >
          {s.status === "running" ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <span className={cn("size-1.5 rounded-full", s.status === "done" ? "bg-emerald-500" : "bg-zinc-400")} />
          )}
          {ENGINE_NAME[name] ?? name}
          {s.status === "done" && <span className="tabular-nums opacity-70">{s.found}</span>}
        </span>
      ))}
      <span className="ml-auto tabular-nums text-muted-foreground">
        {running && "checking for a newer model…"}
      </span>
    </div>
  );
}
