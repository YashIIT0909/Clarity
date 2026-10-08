"use client";

import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  CheckCircle2,
  CircleDashed,
  ExternalLink,
  ImagePlus,
  Loader2,
  OctagonX,
  ShieldAlert,
  ShieldCheck,
  Store,
  Timer,
  TrendingUp,
  X,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  boardReasons,
  EvidenceBoard,
  isPhotoSummary,
  reduceForensics,
  type Forensics,
  type ForensicsEvent,
} from "@/components/evidence-board";
import { isLifecycleReason, reduceLifecycle, SuccessorRadar, type Lifecycle, type LifecycleEvent } from "@/components/successor-radar";
import {
  failLiveRun,
  OwnerReceipts,
  reduceReceipts,
  startLiveRun,
  type Receipts,
  type ReceiptsEvent,
} from "@/components/owner-receipts";
import { compressForUpload, IMAGE_TYPES, MAX_UPLOAD_BYTES } from "@/lib/image";
import { cn } from "@/lib/utils";

const API = process.env.NEXT_PUBLIC_API_URL;
const REVEAL_GAP_MS = 700; // pace the chain so each step is readable on stage
const FORENSICS_GAP_MS = 120; // evidence events stream fast: the board animates them instead
// Pre-verified screenshot (synthetic ad; its Lens results are cached). Uploaded byte-for-byte, never re-compressed,
// so the demo replays from cache and is deterministic.
const DEMO_SCREENSHOT = { label: "Screenshot: Sony XM5 ad at $59", src: "/demo/ad_sony_xm5_59.png", price: "59" };

type Stage = "resolve" | "trust" | "gate" | "timing" | "narrator" | "verdict";
type Verdict = "walk_away" | "wait" | "buy_now";
// Where to buy instead (backend alternatives.py): a major retailer at a normal market price. Information only.
type Alternative = { store: string; price: number; link: string; saving: number | null; text: string };
type Evt = {
  stage: Stage | "done" | "error" | "forensics" | "lifecycle" | "receipts" | "owners";
  type?: string;
  identified_as?: string;
  ad_price?: number | null;
  store_domain?: string | null;
  store_note?: string | null;
  price_note?: string;
  status?: "running" | "done";
  message?: string;
  label?: string;
  marketplace?: string;
  listing_id?: string;
  verdict?: "pass" | "fail" | "buy" | "wait";
  decision?: "stop" | "proceed";
  reasons?: string[];
  confidence?: number | null;
  judged?: boolean; // timing step: false = no data for any timing component
  timing_judged?: boolean | null; // verdict step
  final_verdict?: Verdict;
  headline?: string;
  summary?: string;
  steps?: { title: string; text: string }[];
  alternative?: Alternative | null; // verdict step
};
type Node = { status: "pending" | "running" | "done" | "skipped"; data?: Evt };
type Demo = { key: string; label: string; url: string };
type Picked = { blob: Blob; url: string; name: string; demo?: boolean };

const CHAIN: { stage: Stage; title: string; icon: typeof ShieldCheck }[] = [
  { stage: "resolve", title: "Find the listing", icon: CircleDashed },
  { stage: "trust", title: "Trust check", icon: ShieldCheck },
  { stage: "gate", title: "Gate", icon: ArrowRight },
  { stage: "timing", title: "Timing check", icon: TrendingUp },
  { stage: "narrator", title: "Compose verdict", icon: Timer },
];

const emptyChain = () =>
  Object.fromEntries(CHAIN.map((c) => [c.stage, { status: "pending" }])) as Record<Stage, Node>;

const VERDICT_STYLE: Record<Verdict, { label: string; icon: typeof ShieldCheck; card: string; accent: string }> = {
  walk_away: {
    label: "Walk away",
    icon: OctagonX,
    card: "border-red-500/40 bg-red-500/5",
    accent: "text-red-600 dark:text-red-400",
  },
  wait: {
    label: "Wait",
    icon: Timer,
    card: "border-amber-500/40 bg-amber-500/5",
    accent: "text-amber-600 dark:text-amber-400",
  },
  buy_now: {
    label: "Buy now",
    icon: CheckCircle2,
    card: "border-emerald-500/40 bg-emerald-500/5",
    accent: "text-emerald-600 dark:text-emerald-400",
  },
};

export default function Home() {
  const [input, setInput] = useState("");
  const [demos, setDemos] = useState<Demo[]>([]);
  const [chain, setChain] = useState(emptyChain);
  const [final, setFinal] = useState<Evt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [forensics, setForensics] = useState<Forensics | null>(null);
  const [radar, setRadar] = useState<Lifecycle | null>(null);
  const [receipts, setReceipts] = useState<Receipts | null>(null);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [price, setPrice] = useState("");
  const [dragging, setDragging] = useState(false);

  const source = useRef<EventSource | null>(null);
  const receiptsSource = useRef<EventSource | null>(null); // the on-request "See what owners say" run
  const queue = useRef<Evt[]>([]);
  const lastReveal = useRef(0);
  const shotUrl = useRef<string | null>(null); // the screenshot being analysed, pinned beside the photo wall
  const fileInput = useRef<HTMLInputElement>(null);

  function apply(e: Evt) {
    if (e.stage === "forensics") {
      setForensics((f) => reduceForensics(f, e as ForensicsEvent, shotUrl.current));
      return;
    }
    if (e.stage === "lifecycle") {
      setRadar((l) => reduceLifecycle(l, e as LifecycleEvent));
      return;
    }
    if (e.stage === "receipts" || e.stage === "owners") {
      setReceipts((r) => reduceReceipts(r, e as ReceiptsEvent));
      return;
    }
    if (e.stage === "error") {
      setError(e.message ?? "Something went wrong.");
      setBusy(false);
      setChain((c) => {
        const next = { ...c };
        for (const s of Object.keys(next) as Stage[]) if (next[s].status === "running") next[s] = { status: "pending" };
        return next;
      });
      return;
    }
    if (e.stage === "done") {
      setBusy(false);
      return;
    }
    if (e.stage === "verdict") {
      setFinal((f) => ({ ...f, ...e }));
      return;
    }
    if (e.stage === "narrator" && e.status === "done") setFinal((f) => ({ ...f, ...e, stage: "verdict" }));
    setChain((c) => {
      const next = { ...c, [e.stage]: { status: e.status ?? "done", data: e } };
      if (e.stage === "gate" && e.decision === "stop") next.timing = { status: "skipped" };
      return next;
    });
  }

  useEffect(() => {
    fetch(`${API}/demo-products`)
      .then((r) => r.json())
      .then(setDemos)
      .catch(() => setError("Can't reach the Clarity backend."));
    // Leaving the page drops a live receipts run: if the page comes back (back/forward cache), offer a retry.
    const hide = () => {
      if (receiptsSource.current) {
        receiptsSource.current.close();
        receiptsSource.current = null;
        setReceipts((r) => (r?.run?.state === "running" ? failLiveRun(r, "left the page") : r));
      }
    };
    window.addEventListener("pagehide", hide);
    return () => {
      source.current?.close();
      receiptsSource.current?.close();
      window.removeEventListener("pagehide", hide);
    };
  }, []);

  // Reveal queued events one at a time so fast (cached) runs still read as a chain.
  useEffect(() => {
    const id = setInterval(() => {
      const gap = ["forensics", "lifecycle", "receipts"].includes(queue.current[0]?.stage) ? FORENSICS_GAP_MS : REVEAL_GAP_MS;
      if (!queue.current.length || Date.now() - lastReveal.current < gap) return;
      const e = queue.current.shift()!;
      lastReveal.current = Date.now();
      apply(e);
    }, 50);
    return () => clearInterval(id);
  }, []);

  function reset() {
    source.current?.close();
    receiptsSource.current?.close();
    receiptsSource.current = null;
    queue.current = [];
    setChain(emptyChain());
    setFinal(null);
    setError(null);
    setForensics(null);
    setRadar(null);
    setReceipts(null);
    setBusy(true);
  }

  function analyze(q: string) {
    if (!q.trim() || busy) return;
    reset();
    shotUrl.current = null;
    listen(`${API}/analyze/stream?q=${encodeURIComponent(q.trim())}`);
  }

  function pick(file: Blob, name: string, demo = false) {
    if (!IMAGE_TYPES.includes(file.type)) return setError("Use a PNG, JPG or WebP screenshot.");
    if (file.size > MAX_UPLOAD_BYTES) return setError("That image is larger than 10 MB.");
    if (picked) URL.revokeObjectURL(picked.url);
    setError(null);
    const next = { blob: file, url: URL.createObjectURL(file), name, demo };
    setPicked(next);
    return next;
  }

  async function analyzeImage(img: Picked, adPrice: string) {
    if (busy) return;
    reset();
    shotUrl.current = img.url;
    try {
      const form = new FormData();
      // Demo screenshots go up byte-for-byte (cached); anything else is shrunk under 500 KB first.
      const body = img.demo ? img.blob : await compressForUpload(img.blob);
      form.append("file", body, body === img.blob ? img.name : "screenshot.jpg");
      if (adPrice.trim()) form.append("price", adPrice.trim());
      const r = await fetch(`${API}/analyze/image`, { method: "POST", body: form });
      const j = await r.json();
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "The screenshot couldn't be uploaded.");
      listen(`${API}/analyze/image/stream/${j.job_id}`);
    } catch (err) {
      queue.current.push({ stage: "error", message: err instanceof Error ? err.message : "Upload failed." });
    }
  }

  /** "See what owners say": the live receipts run for this query, streamed into the card. */
  function requestReceipts() {
    const qid = receipts?.queryId;
    if (!qid || receiptsSource.current) return;
    setReceipts((r) => r && startLiveRun(r));
    const es = new EventSource(`${API}/receipts/stream/${qid}`);
    receiptsSource.current = es;
    es.onmessage = (m) => {
      const e: Evt = JSON.parse(m.data);
      queue.current.push(e);
      if (e.stage === "owners" || (e.stage === "receipts" && e.type === "error")) {
        es.close();
        receiptsSource.current = null;
      }
    };
    es.onerror = () => {
      if (es.readyState !== EventSource.CLOSED) es.close();
      if (receiptsSource.current === es) {
        receiptsSource.current = null;
        setReceipts((r) => r && failLiveRun(r, "lost connection"));
      }
    };
  }

  function listen(url: string) {
    const es = new EventSource(url);
    source.current = es;
    es.onmessage = (m) => {
      const e: Evt = JSON.parse(m.data);
      queue.current.push(e);
      if (e.stage === "done" || e.stage === "error") es.close();
    };
    es.onerror = () => {
      if (es.readyState !== EventSource.CLOSED) {
        es.close();
        queue.current.push({ stage: "error", message: "Lost connection to the Clarity backend." });
      }
    };
  }

  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-8 px-4 py-10 sm:px-6 sm:py-14">
      <header className="flex flex-col gap-2">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Clarity</h1>
        <p className="text-muted-foreground">
          Buy now, wait, or walk away. Clarity checks whether a listing is trustworthy first — and only then whether
          it’s a good time to buy.
        </p>
      </header>

      <section className="flex flex-col gap-3">
        <form
          className={cn(
            "flex flex-col gap-2 rounded-xl border border-transparent p-0 transition-colors",
            dragging && "border-dashed border-foreground/40 bg-muted/50 p-2",
          )}
          onSubmit={(e) => {
            e.preventDefault();
            if (picked) analyzeImage(picked, price);
            else analyze(input);
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const file = e.dataTransfer.files[0];
            if (file) pick(file, file.name);
          }}
        >
          <label htmlFor="q" className="text-sm font-medium">
            Paste a product link or describe the product — or drop, paste or pick a screenshot of an ad
          </label>
          <div className="flex flex-col gap-2 sm:flex-row">
            {picked ? (
              <div className="flex h-11 min-w-0 flex-1 items-center gap-2 rounded-lg border px-2 text-sm">
                {/* eslint-disable-next-line @next/next/no-img-element -- local object URL preview */}
                <img src={picked.url} alt="" className="size-8 shrink-0 rounded object-cover" />
                <span className="min-w-0 flex-1 truncate">{picked.name}</span>
                <button
                  type="button"
                  aria-label="Remove screenshot"
                  onClick={() => {
                    URL.revokeObjectURL(picked.url);
                    setPicked(null);
                    setPrice("");
                  }}
                  className="rounded p-1 text-muted-foreground hover:bg-muted"
                >
                  <X className="size-4" />
                </button>
              </div>
            ) : (
              <Input
                id="q"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPaste={(e) => {
                  const file = e.clipboardData.files[0];
                  if (file) {
                    e.preventDefault();
                    pick(file, file.name || "pasted screenshot");
                  }
                }}
                placeholder="https://www.amazon.com/dp/… or “Sony WH-1000XM5 headphones”"
                className="h-11 text-base"
                autoComplete="off"
              />
            )}
            <input
              ref={fileInput}
              type="file"
              accept={IMAGE_TYPES.join(",")}
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) pick(file, file.name);
                e.target.value = "";
              }}
            />
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                className="h-11"
                aria-label="Upload a screenshot"
                title="Upload a screenshot"
                onClick={() => fileInput.current?.click()}
              >
                <ImagePlus />
              </Button>
              <Button type="submit" className="h-11 flex-1 px-6 sm:flex-none" disabled={busy || (!picked && !input.trim())}>
                {busy ? <Loader2 className="animate-spin" /> : "Analyze"}
              </Button>
            </div>
          </div>
          {picked && (
            <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
              <label htmlFor="price" className="text-sm font-medium">
                Price in the ad <span className="font-normal text-muted-foreground">(optional)</span>
              </label>
              <Input
                id="price"
                inputMode="decimal"
                value={price}
                onChange={(e) => setPrice(e.target.value.replace(/[^0-9.]/g, ""))}
                placeholder="$"
                className="h-9 w-32"
              />
              <span className="text-xs text-muted-foreground">
                Clarity doesn’t read prices from screenshots — type the price shown to have it checked.
              </span>
            </div>
          )}
        </form>
        {demos.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted-foreground">Try a verified example:</span>
            {demos.map((d) => (
              <button
                key={d.key}
                type="button"
                disabled={busy}
                onClick={() => {
                  setInput(d.url);
                  analyze(d.url);
                }}
                className="rounded-full border px-3 py-1 transition-colors hover:bg-muted disabled:opacity-50"
              >
                {d.label}
              </button>
            ))}
            <button
              type="button"
              disabled={busy}
              onClick={async () => {
                const blob = await fetch(DEMO_SCREENSHOT.src).then((r) => r.blob());
                const img = pick(blob, DEMO_SCREENSHOT.src.split("/").pop()!, true);
                if (!img) return;
                setPrice(DEMO_SCREENSHOT.price);
                analyzeImage(img, DEMO_SCREENSHOT.price);
              }}
              className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1 transition-colors hover:bg-muted disabled:opacity-50"
            >
              <ImagePlus className="size-3.5" />
              {DEMO_SCREENSHOT.label}
            </button>
          </div>
        )}
      </section>

      {error && (
        <div role="alert" data-testid="error" className="flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/5 p-4 text-sm">
          <XCircle className="mt-0.5 size-4 shrink-0 text-red-600 dark:text-red-400" />
          {error}
        </div>
      )}

      <section className="grid gap-6 md:grid-cols-[1fr_1.1fr]">
        <ReasoningChain chain={chain} forensics={forensics} radar={radar} busy={busy} />
        <VerdictCard final={final} busy={busy} />
      </section>

      {/* The chain's final section, shown after the verdict. Timing already scored its confirmed defects (wait-only, weight 0.10);
          an on-request live run comes later and never re-scores the verdict. */}
      {receipts && final?.final_verdict && <OwnerReceipts r={receipts} onRequest={requestReceipts} />}
    </main>
  );
}

function ReasoningChain({
  chain,
  forensics,
  radar,
  busy,
}: {
  chain: Record<Stage, Node>;
  forensics: Forensics | null;
  radar: Lifecycle | null;
  busy: boolean;
}) {
  return (
    <div className="flex flex-col gap-1" aria-live="polite">
      <h2 className="mb-2 text-sm font-medium uppercase tracking-wide text-muted-foreground">Reasoning chain</h2>
      <ol className="relative flex flex-col">
        {CHAIN.map(({ stage, title, icon: Icon }, i) => {
          const node = chain[stage];
          const d = node.data;
          const failed = (stage === "trust" && d?.verdict === "fail") || (stage === "gate" && d?.decision === "stop");
          const passed = (stage === "trust" && d?.verdict === "pass") || (stage === "gate" && d?.decision === "proceed");
          return (
            <li key={stage} className="relative flex gap-3 pb-5 last:pb-0">
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
              <div
                className={cn(
                  "flex min-w-0 flex-1 flex-col gap-1 pt-1 animate-in fade-in slide-in-from-left-1",
                  node.status === "pending" && "opacity-40",
                )}
              >
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
                {stage === "trust" && forensics && node.status !== "pending" && <EvidenceBoard f={forensics} running={busy} />}
                {stage === "timing" && radar && node.status !== "pending" && node.status !== "skipped" && (
                  <SuccessorRadar l={radar} running={busy} />
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
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
  if (node.status === "skipped") return tag("skipped", "muted");
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
    return <p className="text-sm text-muted-foreground">Not run. A risky listing isn’t worth buying at any price or time.</p>;
  if (!d) return null;
  if (node.status === "running") return <p className="text-sm text-muted-foreground">{d.message}</p>;
  if (stage === "resolve" && d.marketplace === "screenshot")
    return (
      <div className="flex flex-col gap-0.5 text-sm text-muted-foreground">
        <p>
          Identified from the screenshot: <span className="text-foreground">{d.identified_as}</span>
          {d.ad_price != null && <> · price in the ad ${d.ad_price}</>}
        </p>
        {d.store_note && <p className="text-xs">{d.store_note}</p>}
        <p className="text-xs">{d.price_note}</p>
      </div>
    );
  if (stage === "resolve")
    return (
      <p className="text-sm text-muted-foreground">
        {d.label} · {d.marketplace === "ebay" ? "eBay" : "Amazon"} {d.listing_id}
      </p>
    );
  if (stage === "gate") return <p className="text-sm">{d.message}</p>;
  if (stage === "narrator") return <p className="text-sm text-muted-foreground">Verdict written from the signals above.</p>;
  return (
    <ul className="flex flex-col gap-1 text-sm text-muted-foreground">
      {(d.reasons ?? []).filter((r) => !(hide && (hide.has(r) || isPhotoSummary(r))) && !hideIf?.(r)).map((r) => (
        <li key={r} className="flex gap-2">
          <span aria-hidden>·</span>
          <span>{r}</span>
        </li>
      ))}
    </ul>
  );
}

function VerdictCard({ final, busy }: { final: Evt | null; busy: boolean }) {
  if (!final?.final_verdict)
    return (
      <div className="flex min-h-64 items-center justify-center rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
        {busy ? "Working through the checks…" : "Your verdict will appear here."}
      </div>
    );

  const v = VERDICT_STYLE[final.final_verdict];
  const Icon = v.icon;
  return (
    <div className={cn("flex flex-col gap-5 rounded-xl border p-6 animate-in fade-in zoom-in-95", v.card)}>
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <div data-testid="verdict" className={cn("flex items-center gap-2 text-3xl font-semibold tracking-tight", v.accent)}>
            <Icon className="size-7" />
            {final.timing_judged === false ? "Safe to buy — timing not judged" : v.label}
          </div>
          {final.headline && <p className="text-base">{final.headline}</p>}
        </div>
        {final.confidence != null && (
          <div className="flex shrink-0 flex-col items-end">
            <span className={cn("text-2xl font-semibold tabular-nums", v.accent)}>
              {Math.round(final.confidence * 100)}%
            </span>
            <span className="text-xs text-muted-foreground">confidence</span>
          </div>
        )}
      </div>

      {final.summary && <p className="text-sm leading-relaxed">{final.summary}</p>}

      {final.alternative && <BuyHereInstead a={final.alternative} />}

      {final.steps && final.steps.length > 0 && (
        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-medium uppercase tracking-wide text-muted-foreground">Why</h3>
          <ol className="flex flex-col gap-3">
            {final.steps.map((s, i) => (
              <li key={i} className="flex gap-3 text-sm">
                <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-foreground/10 text-xs font-medium tabular-nums">
                  {i + 1}
                </span>
                <div>
                  <span className="font-medium">{s.title}.</span> <span className="text-muted-foreground">{s.text}</span>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

function BuyHereInstead({ a }: { a: Alternative }) {
  return (
    <div data-testid="alternative" className="flex flex-col gap-2 rounded-lg border bg-background/60 p-4">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Store className="size-4 text-emerald-600 dark:text-emerald-400" />
        Buy it here instead
      </div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="text-lg font-semibold">
          {a.store} · <span className="tabular-nums">${a.price.toFixed(2)}</span>
        </span>
        {a.saving != null && (
          <span className="text-sm text-emerald-700 tabular-nums dark:text-emerald-400">${a.saving.toFixed(2)} less</span>
        )}
      </div>
      <p className="text-sm text-muted-foreground">{a.text}</p>
      <a
        href={a.link}
        target="_blank"
        rel="noopener noreferrer nofollow"
        className="inline-flex w-fit items-center gap-1 text-sm font-medium underline decoration-foreground/30 underline-offset-2 hover:decoration-foreground"
      >
        Open at {a.store}
        <ExternalLink className="size-3.5" />
      </a>
    </div>
  );
}
