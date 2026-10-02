/**
 * Clarity's MCP App view: the verdict card, then the reasoning chain replayed from the recorded event log
 * (result._meta.events, each with the t_ms it was emitted at). Never a fake live run: it says it's a replay.
 * Visual language copied from frontend/src/app/page.tsx (verdict card, chain) and frontend/src/components.
 */
import { Component, StrictMode, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import type { App, McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import { useApp, useHostStyles } from "@modelcontextprotocol/ext-apps/react";
import {
  ArrowRight,
  MessageCircleQuestion,
  CheckCircle2,
  CircleDashed,
  Loader2,
  OctagonX,
  ShieldAlert,
  ShieldCheck,
  Timer,
  TrendingUp,
  XCircle,
} from "lucide-react";
import { boardReasons, EvidenceBoard, isPhotoSummary, reduceForensics, type Forensics, type ForensicsEvent } from "./evidence-board";
import { isLifecycleReason, reduceLifecycle, SuccessorRadar, type Lifecycle, type LifecycleEvent } from "./successor-radar";
import { failLiveRun, OwnerReceipts, reduceReceipts, startLiveRun, type Receipts, type ReceiptsEvent } from "./owner-receipts";
import { cn } from "./cn";
import "./app.css";

type Stage = "resolve" | "trust" | "gate" | "timing" | "narrator";
type Evt = {
  t_ms: number;
  stage: string;
  type?: string;
  status?: "running" | "done";
  message?: string;
  label?: string;
  marketplace?: string;
  listing_id?: string;
  verdict?: "pass" | "fail" | "buy" | "wait";
  decision?: "stop" | "proceed";
  reasons?: string[];
  confidence?: number | null;
  judged?: boolean;
  headline?: string;
  steps?: { title: string; text: string }[];
  [k: string]: unknown;
};
type Verdict = "walk_away" | "buy_now" | "wait" | "buy_timing_not_judged" | "could_not_check";
type Clarity = {
  verdict: Verdict;
  query_id: string | null;
  summary: string;
  reason?: string;
  timing: { confidence: number | null } | null;
  data_as_of: { listing: string | null } | null;
  unavailable: Record<string, string> | null;
};
type ToolResult = { structuredContent?: Clarity; _meta?: { events?: Evt[] }; content?: { type: string; text?: string }[]; isError?: boolean };
type Node = { status: "pending" | "running" | "done" | "skipped"; data?: Evt };
type View =
  | { kind: "waiting"; listing?: string }
  | { kind: "result"; c: Clarity; events: Evt[]; listing?: string }
  | { kind: "failed"; reason: string; events: Evt[] };

const CHAIN: { stage: Stage; title: string; icon: typeof ShieldCheck }[] = [
  { stage: "resolve", title: "Find the listing", icon: CircleDashed },
  { stage: "trust", title: "Trust check", icon: ShieldCheck },
  { stage: "gate", title: "Gate", icon: ArrowRight },
  { stage: "timing", title: "Timing check", icon: TrendingUp },
  { stage: "narrator", title: "Compose verdict", icon: Timer },
];
const VERDICT_STYLE: Record<Verdict, { label: string; icon: typeof ShieldCheck; card: string; accent: string }> = {
  walk_away: { label: "Walk away", icon: OctagonX, card: "border-red-500/40 bg-red-500/5", accent: "text-red-600 dark:text-red-400" },
  wait: { label: "Wait", icon: Timer, card: "border-amber-500/40 bg-amber-500/5", accent: "text-amber-600 dark:text-amber-400" },
  buy_now: { label: "Buy now", icon: CheckCircle2, card: "border-emerald-500/40 bg-emerald-500/5", accent: "text-emerald-600 dark:text-emerald-400" },
  buy_timing_not_judged: {
    label: "Safe to buy — timing not judged",
    icon: CheckCircle2,
    card: "border-emerald-500/40 bg-emerald-500/5",
    accent: "text-emerald-600 dark:text-emerald-400",
  },
  could_not_check: { label: "Couldn't check", icon: XCircle, card: "border-zinc-500/40 bg-muted/40", accent: "text-foreground" },
};
// `unavailable` entries the chain already shows (gate line, radar calm line) or that aren't a check that didn't run.
const SHOWN_ELSEWHERE = new Set(["timing", "evidence.successor", "data_as_of.retail_stores", "everything"]);
const UNAVAILABLE_LABEL: Record<string, string> = { "evidence.price.median": "Market price" };
const NOT_A_PASS = "This is not a pass; don't treat the listing as verified.";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// "29 Sep 2026", the same form as the summary line the agent reads (contract.py).
const day = (iso?: string | null) => {
  const d = iso ? new Date(iso) : null;
  return d ? `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}` : null;
};
const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} s`;

function ClarityApp() {
  const [view, setView] = useState<View>({ kind: "waiting" });
  const [ctx, setCtx] = useState<McpUiHostContext | undefined>();
  const { app, error } = useApp({
    appInfo: { name: "Clarity", version: "1.0.0" },
    capabilities: {},
    onAppCreated: (app) => {
      app.addEventListener("toolinput", (p) => setView({ kind: "waiting", listing: String(p.arguments?.listing ?? "") }));
      app.addEventListener("toolresult", (r) =>
        setView((v) => ({ ...fromResult(r as ToolResult), listing: v.kind === "waiting" ? v.listing : undefined }) as View),
      );
      // Trap #1: a cancelled check reads as "Couldn't check", never a blank or spinning view.
      app.addEventListener("toolcancelled", (p) =>
        setView({ kind: "failed", reason: `the check was cancelled${p.reason ? ` (${p.reason})` : ""}`, events: [] }),
      );
      app.addEventListener("hostcontextchanged", (p) => setCtx((c) => ({ ...c, ...p })));
    },
  });
  useEffect(() => {
    if (app) setCtx(app.getHostContext());
  }, [app]);
  useHostStyles(app, app?.getHostContext());
  useOpenLinksThroughHost(app);
  const maxHeight = useContainerMaxHeight(ctx);

  if (error) return <Failed reason={`the view couldn't connect to the host (${error.message})`} />;
  if (view.kind === "waiting")
    return (
      <main className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
        <Loader2 className="size-4 shrink-0 animate-spin" />
        <span className="min-w-0 break-words">Clarity is checking {view.listing || "this listing"}…</span>
      </main>
    );
  if (view.kind === "failed")
    return (
      <main className="flex flex-col gap-4 p-3">
        <Failed reason={view.reason} />
        {view.events.length > 0 && (
          <Guard>
            <Chain events={view.events} maxHeight={maxHeight} unavailable={null} />
          </Guard>
        )}
      </main>
    );
  return (
    <main className="flex flex-col gap-4 p-3">
      <VerdictCard c={view.c} events={view.events} />
      {app && <AskWhy app={app} c={view.c} listing={view.listing} />}
      {/* Receipts never run on a walk-away: no button there. */}
      {app && view.c.verdict !== "walk_away" && view.c.query_id && <Owners app={app} queryId={view.c.query_id} />}
      <Guard>
        <Chain
          key={`${view.c.summary}|${view.events.length}`}
          events={view.events}
          maxHeight={maxHeight}
          unavailable={view.c.unavailable}
          gateStopped={view.c.verdict === "walk_away"}
        />
      </Guard>
    </main>
  );
}

/** A bug in the evidence view must never hide the verdict card above it. */
class Guard extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <p className="rounded-md bg-muted/60 px-3 py-2 text-sm text-muted-foreground">The evidence couldn't be displayed; the verdict above stands.</p>
    ) : (
      this.props.children
    );
  }
}

/** "Ask Claude why": a follow-up message into the conversation, only on hosts that accept ui/message. */
function AskWhy({ app, c, listing }: { app: App; c: Clarity; listing?: string }) {
  const [sent, setSent] = useState<"no" | "yes" | "failed">("no");
  if (!app.getHostCapabilities()?.message) return null;
  const ask = async () => {
    const text = `Why did Clarity say "${VERDICT_STYLE[c.verdict].label}" for ${listing || "this listing"}? Walk me through the main reasons from its check.`;
    try {
      const r = await app.sendMessage({ role: "user", content: [{ type: "text", text }] });
      setSent(r.isError ? "failed" : "yes");
    } catch {
      setSent("failed");
    }
  };
  return (
    <button
      type="button"
      onClick={ask}
      disabled={sent === "yes"}
      className="inline-flex items-center gap-1.5 self-start rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-60"
    >
      <MessageCircleQuestion className="size-4" />
      {sent === "yes" ? "Asked in the chat" : sent === "failed" ? "Couldn't send; try again" : "Ask Claude why"}
    </button>
  );
}

/** "What owners say" through the app-only tool clarity_owner_receipts (context only, never part of the verdict). */
function Owners({ app, queryId }: { app: App; queryId: string }) {
  const [r, setR] = useState<Receipts | null>(null);
  const [state, setState] = useState<"idle" | "loading" | string>("idle"); // any other string: a calm error line
  if (!app.getHostCapabilities()?.serverTools) return null;
  const ask = async (live: boolean) => {
    if (live) setR((x) => x && startLiveRun(x));
    else setState("loading");
    try {
      const res = await app.callServerTool({ name: "clarity_owner_receipts", arguments: { query_id: queryId, live } });
      const out = res.structuredContent as { status: string; message?: string; events: ReceiptsEvent[] } | undefined;
      if (!out || out.status === "error" || out.status === "running") {
        const why = out?.message ?? "no answer from Clarity";
        if (r || live) setR((x) => x && failLiveRun(x, why));
        else setState(`What owners say isn't available: ${why}.`);
        return;
      }
      setR(out.events.reduce<Receipts | null>((x, e) => reduceReceipts(x, e), null));
      setState("idle");
    } catch (e) {
      const why = e instanceof Error ? e.message : "the request failed";
      if (live) setR((x) => x && failLiveRun(x, why));
      else setState(`What owners say isn't available: ${why}.`);
    }
  };
  if (r) return <OwnerReceipts r={r} onRequest={() => ask(true)} />;
  if (state !== "idle" && state !== "loading") return <p className="rounded-md bg-muted/60 px-3 py-2 text-sm text-muted-foreground">{state}</p>;
  return (
    <button
      type="button"
      onClick={() => ask(false)}
      disabled={state === "loading"}
      className="inline-flex items-center gap-1.5 self-start rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-60"
    >
      {state === "loading" && <Loader2 className="size-4 animate-spin" />}
      What owners say
      <span className="text-xs text-muted-foreground">· reviews and forums, context only</span>
    </button>
  );
}

function fromResult(r: ToolResult): View {
  const c = r.structuredContent;
  const events = r._meta?.events ?? [];
  if (!c) {
    const text = r.content?.find((b) => b.type === "text")?.text;
    return { kind: "failed", reason: text || "the tool returned no result", events };
  }
  if (c.verdict === "could_not_check") return { kind: "failed", reason: c.reason ?? "unknown reason", events };
  return { kind: "result", c, events };
}

/** Links can't open from a sandboxed iframe: hand them to the host (ui/open-link). */
function useOpenLinksThroughHost(app: App | null) {
  useEffect(() => {
    if (!app) return;
    const onClick = (e: MouseEvent) => {
      const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a) return;
      e.preventDefault();
      if (app.getHostCapabilities()?.openLinks) void app.openLink({ url: a.href });
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [app]);
}

/** The host's maxHeight, read at runtime (it can change). The document scrolls inside it instead of being cut off. */
function useContainerMaxHeight(ctx?: McpUiHostContext): number | undefined {
  const dims = ctx?.containerDimensions as { maxHeight?: number; height?: number } | undefined;
  const max = dims?.height ?? dims?.maxHeight;
  useEffect(() => {
    const html = document.documentElement;
    html.style.maxHeight = max ? `${max}px` : "";
    html.style.overflowY = max ? "auto" : "";
  }, [max]);
  return max;
}

function Failed({ reason }: { reason: string }) {
  const v = VERDICT_STYLE.could_not_check;
  return (
    <div data-testid="verdict-card" className={cn("flex flex-col gap-2 rounded-xl border p-4", v.card)}>
      <div data-testid="verdict" className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
        <XCircle className="size-6 shrink-0" />
        {v.label}
      </div>
      <p className="text-sm">Clarity couldn't check this listing ({reason}).</p>
      <p className="text-sm font-medium">{NOT_A_PASS}</p>
    </div>
  );
}

function VerdictCard({ c, events }: { c: Clarity; events: Evt[] }) {
  const v = VERDICT_STYLE[c.verdict];
  const Icon = v.icon;
  const story = events.find((e) => e.stage === "narrator" && e.status === "done");
  const confidence = c.timing?.confidence;
  const asOf = day(c.data_as_of?.listing);
  return (
    <div data-testid="verdict-card" className={cn("flex flex-col gap-4 rounded-xl border p-4 animate-in fade-in zoom-in-95", v.card)}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <div data-testid="verdict" className={cn("flex items-center gap-2 text-2xl font-semibold tracking-tight", v.accent)}>
            <Icon className="size-6 shrink-0" />
            <span className="min-w-0">{v.label}</span>
          </div>
          {story?.headline && <p className="text-base">{story.headline}</p>}
          {asOf && <p className="text-xs text-muted-foreground">Prices as of {asOf}</p>}
        </div>
        {confidence != null && (
          <div className="flex shrink-0 flex-col items-end">
            <span className={cn("text-2xl font-semibold tabular-nums", v.accent)}>{Math.round(confidence * 100)}%</span>
            <span className="text-xs text-muted-foreground">confidence</span>
          </div>
        )}
      </div>
      {story?.steps && story.steps.length > 0 && (
        <ol className="flex flex-col gap-2">
          {story.steps.map((s, i) => (
            <li key={i} className="flex gap-2 text-sm">
              <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-foreground/10 text-xs font-medium tabular-nums">
                {i + 1}
              </span>
              <div className="min-w-0">
                <span className="font-medium">{s.title}.</span> <span className="text-muted-foreground">{s.text}</span>
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/** Folds the events up to `now` (replay clock, ms) into the chain, the Evidence Board and the Successor Radar. */
function fold(events: Evt[], now: number) {
  const chain = Object.fromEntries(CHAIN.map((c) => [c.stage, { status: "pending" } as Node])) as Record<Stage, Node>;
  let forensics: Forensics | null = null;
  let radar: Lifecycle | null = null;
  for (const e of events) {
    if (e.t_ms > now) break;
    if (e.stage === "forensics") forensics = reduceForensics(forensics, e as unknown as ForensicsEvent, null);
    else if (e.stage === "lifecycle") radar = reduceLifecycle(radar, e as unknown as LifecycleEvent);
    else if (e.stage in chain) {
      chain[e.stage as Stage] = { status: e.status === "running" ? "running" : "done", data: e };
      if (e.stage === "gate" && e.decision === "stop") chain.timing = { status: "skipped" };
    }
  }
  return { chain, forensics, radar };
}

function Chain({
  events,
  maxHeight,
  unavailable,
  gateStopped,
}: {
  events: Evt[];
  maxHeight?: number;
  unavailable: Record<string, string> | null;
  gateStopped?: boolean;
}) {
  const duration = events.length ? events[events.length - 1].t_ms : 0;
  const [now, setNow] = useState(duration); // first render: the finished chain, measured below, before any paint
  const [open, setOpen] = useState<boolean | null>(null); // null = not decided yet
  const replaying = now < duration;

  // Fit the host: if the finished chain doesn't fit under maxHeight, it starts collapsed behind "Show evidence".
  useLayoutEffect(() => {
    if (open !== null) return;
    const fits = !maxHeight || document.documentElement.scrollHeight <= maxHeight;
    setOpen(fits);
    if (fits) setNow(0);
  }, [open, maxHeight]);

  // Replay at the recorded pace: each event appears at the time it was emitted in the real check.
  useEffect(() => {
    if (!open || !replaying) return;
    const start = performance.now() - now;
    const id = setInterval(() => setNow(Math.min(duration, performance.now() - start)), 100);
    return () => clearInterval(id);
  }, [open, replaying, duration]); // eslint-disable-line react-hooks/exhaustive-deps

  const { chain, forensics, radar } = useMemo(() => fold(events, now), [events, now]);
  const extra = Object.entries(unavailable ?? {}).filter(([k]) => !SHOWN_ELSEWHERE.has(k));

  if (open === false)
    return (
      <button
        type="button"
        onClick={() => {
          setNow(0);
          setOpen(true);
        }}
        className="self-start rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
      >
        Show evidence · replay of this check ({seconds(duration)})
      </button>
    );

  return (
    <section className="flex min-w-0 flex-col gap-1" aria-live="polite">
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1">
        <h2 className="text-sm font-medium uppercase tracking-wide text-muted-foreground">Reasoning chain</h2>
        <span data-testid="replay-label" className="text-xs text-muted-foreground">
          replayed from this check · {seconds(duration)}
        </span>
        {replaying && (
          <button type="button" onClick={() => setNow(duration)} className="ml-auto text-xs underline underline-offset-2">
            Skip replay
          </button>
        )}
      </div>
      <ol className="relative flex min-w-0 flex-col">
        {CHAIN.map(({ stage, title, icon: Icon }, i) => {
          const node = chain[stage];
          const d = node.data;
          const failed = (stage === "trust" && d?.verdict === "fail") || (stage === "gate" && d?.decision === "stop");
          const passed = (stage === "trust" && d?.verdict === "pass") || (stage === "gate" && d?.decision === "proceed");
          return (
            <li key={stage} className="relative flex min-w-0 gap-3 pb-5 last:pb-0">
              {i < CHAIN.length - 1 && (
                <span
                  className={cn(
                    "absolute left-[15px] top-8 h-[calc(100%-2rem)] w-px",
                    node.status === "done" ? "bg-foreground/30" : "bg-foreground/10",
                  )}
                />
              )}
              <span
                className={cn(
                  "z-10 flex size-8 shrink-0 items-center justify-center rounded-full border bg-background",
                  node.status === "pending" && "opacity-40",
                  node.status === "skipped" && "border-dashed opacity-50",
                  failed && "border-red-500 text-red-600 dark:text-red-400",
                  passed && "border-emerald-500 text-emerald-600 dark:text-emerald-400",
                )}
              >
                {node.status === "running" ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : failed && stage === "trust" ? (
                  <ShieldAlert className="size-4" />
                ) : (
                  <Icon className="size-4" />
                )}
              </span>
              <div className={cn("flex min-w-0 flex-1 flex-col gap-1 pt-1 animate-in fade-in", node.status === "pending" && "opacity-40")}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{title}</span>
                  <StageTag stage={stage} node={node} />
                </div>
                <StageBody
                  stage={stage}
                  node={node}
                  hide={stage === "trust" && forensics ? boardReasons(forensics) : undefined}
                  hideIf={stage === "timing" && radar ? isLifecycleReason : undefined}
                />
                {stage === "trust" && forensics && node.status !== "pending" && <EvidenceBoard f={forensics} running={replaying} now={now} />}
                {stage === "trust" && gateStopped && d?.verdict === "fail" && (
                  <p data-testid="gate-line" className="mt-2 rounded-md border border-red-500/40 bg-red-500/5 px-3 py-2 text-sm">
                    Timing check not run — the listing failed the trust check.
                  </p>
                )}
                {stage === "timing" && radar && node.status !== "pending" && node.status !== "skipped" && (
                  <SuccessorRadar l={radar} running={replaying} />
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {!replaying && unavailable === null && (
        <p className="mt-2 rounded-md bg-muted/60 px-3 py-2 text-sm text-muted-foreground">
          The check stopped here: the steps that are still greyed out didn't run.
        </p>
      )}
      {!replaying &&
        extra.map(([k, why]) => (
          <p key={k} className="mt-2 rounded-md bg-muted/60 px-3 py-2 text-sm text-muted-foreground">
            {UNAVAILABLE_LABEL[k] ?? k}: {why}
          </p>
        ))}
    </section>
  );
}

function StageTag({ stage, node }: { stage: Stage; node: Node }) {
  const d = node.data;
  const tag = (text: string, tone: "red" | "green" | "amber" | "muted") => (
    <span
      className={cn(
        "rounded px-1.5 py-0.5 text-xs font-medium",
        tone === "red" && "bg-red-500/10 text-red-700 dark:text-red-400",
        tone === "green" && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
        tone === "amber" && "bg-amber-500/10 text-amber-700 dark:text-amber-400",
        tone === "muted" && "bg-muted text-muted-foreground",
      )}
    >
      {text}
    </span>
  );
  if (node.status === "skipped") return tag("not run", "muted");
  if (node.status !== "done" || !d) return null;
  if (stage === "trust") return d.verdict === "pass" ? tag("passed", "green") : tag("risk detected", "red");
  if (stage === "gate") return d.decision === "stop" ? tag("stopped", "red") : tag("open", "green");
  if (stage === "timing" && d.judged === false) return tag("safe to buy · timing not judged", "green");
  if (stage === "timing")
    return tag(`${d.verdict === "buy" ? "buy now" : "wait"} · ${Math.round((d.confidence ?? 0) * 100)}%`, d.verdict === "buy" ? "green" : "amber");
  return null;
}

function StageBody({ stage, node, hide, hideIf }: { stage: Stage; node: Node; hide?: Set<string>; hideIf?: (r: string) => boolean }) {
  const d = node.data;
  if (node.status === "skipped")
    return <p className="text-sm text-muted-foreground">Not run (see the trust check).</p>;
  if (!d) return null;
  if (node.status === "running") return <p className="text-sm text-muted-foreground">{d.message}</p>;
  if (stage === "resolve")
    return (
      <p className="break-words text-sm text-muted-foreground">
        {d.label} · {d.marketplace === "ebay" ? "eBay" : "Amazon"} {d.listing_id}
      </p>
    );
  if (stage === "gate") return <p className="text-sm">{d.message}</p>;
  if (stage === "narrator") return <p className="text-sm text-muted-foreground">Verdict written from the signals above.</p>;
  return (
    <ul className="flex flex-col gap-1 text-sm text-muted-foreground">
      {(d.reasons ?? [])
        .filter((r) => !(hide && (hide.has(r) || isPhotoSummary(r))) && !hideIf?.(r))
        .map((r) => (
          <li key={r} className="flex gap-2">
            <span aria-hidden>·</span>
            <span className="min-w-0 break-words">{r}</span>
          </li>
        ))}
    </ul>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ClarityApp />
  </StrictMode>,
);
