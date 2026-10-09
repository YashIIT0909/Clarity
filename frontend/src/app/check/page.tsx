"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  CircleDashed,
  ExternalLink,
  ImagePlus,
  Loader2,
  OctagonX,
  ShieldAlert,
  ShieldCheck,
  Store,
  Target,
  Timer,
  TrendingUp,
  X,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { AboutSheet } from "@/components/about-sheet";
import { navLink, SiteNav } from "@/components/site-nav";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EvidenceBoard, isBoardCalm, reduceForensics, type Forensics, type ForensicsEvent } from "@/components/evidence-board";
import { hasSuccessor, reduceLifecycle, SuccessorRadar, type Lifecycle, type LifecycleEvent } from "@/components/successor-radar";
import {
  failLiveRun,
  OwnerReceipts,
  reduceReceipts,
  startLiveRun,
  type Receipts,
  type ReceiptsEvent,
} from "@/components/owner-receipts";
import { DecisionCharts, VerdictGauge, type DecisionResult } from "@/components/decision-charts";
import { compressForUpload, IMAGE_TYPES, MAX_UPLOAD_BYTES } from "@/lib/image";
import { cn } from "@/lib/utils";

const API = process.env.NEXT_PUBLIC_API_URL;
const REVEAL_GAP_MS = 700; // pace the chain so each step is readable on stage
const FORENSICS_GAP_MS = 120; // evidence events stream fast: the board animates them instead
// Pre-verified screenshot (synthetic ad; its Lens results are cached). Uploaded byte-for-byte, never re-compressed,
// so the demo replays from cache and is deterministic.
const DEMO_SCREENSHOTS = [
  { label: "Screenshot: Sony XM5 ad at Rs. 4,999", src: "/demo/ad_sony_xm5_rs4999.png", price: "4999", market: "in" as Market },
  { label: "Screenshot: Sony XM5 ad at Rs. 29,990", src: "/demo/ad_sony_xm5_rs29990.png", price: "29990", market: "in" as Market },
  { label: "Screenshot: Sony XM5 ad at $59", src: "/demo/ad_sony_xm5_59.png", price: "59", market: "us" as Market },
];
// Typed text and screenshots carry no country: the user picks one (India by default). A link decides by itself.
type Market = "in" | "us";
const MARKETS: { code: Market; label: string; symbol: string }[] = [
  { code: "in", label: "India", symbol: "₹" },
  { code: "us", label: "US", symbol: "$" },
];
// The price typed for a screenshot, in its market: "$59" as before, or "₹29,990" (Indian grouping).
const adPrice = (n: number, m?: Market) => (m === "in" ? `₹${Math.round(n).toLocaleString("en-IN")}` : `$${n}`);
const isLink = (q: string) => /^https?:\/\/|amazon\.|ebay\./i.test(q.trim());

type Stage = "resolve" | "trust" | "gate" | "timing" | "narrator" | "verdict";
type Verdict = "walk_away" | "wait" | "buy_now";
// Where to buy instead (backend alternatives.py): a major retailer at a normal market price. Information only.
type Alternative = {
  store: string;
  price: number;
  price_text: string; // in the listing's currency: "$36.24", "₹19,989"
  link: string;
  saving: number | null;
  saving_text: string | null;
  text: string;
};
// What a WAIT waits for (backend timing.wait_plan): a target price and/or when to check again. Never a price forecast.
type WaitPlan = {
  target: { price: number; price_text: string; basis: "store" | "band"; reason: string } | null;
  window: { kind: "successor" | "event" | "season"; date: string | null; days: number | null; text: string } | null;
  text: string;
};
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
  wait_plan?: WaitPlan | null; // verdict step, wait only
  market?: { code: Market; name: string; currency: string; symbol: string }; // resolve + verdict steps: where it's checked
  result?: DecisionResult; // the "verdict" and "done" events: full trust/timing signals
};
type Node = { status: "pending" | "running" | "done" | "skipped"; data?: Evt };
type Demo = { key: string; label: string; url: string; market: Market };
type Picked = { blob: Blob; url: string; name: string; demo?: boolean };

const CHAIN: { stage: Stage; title: string; icon: typeof ShieldCheck }[] = [
  { stage: "resolve", title: "Find the listing", icon: CircleDashed },
  { stage: "trust", title: "Trust check", icon: ShieldCheck },
  { stage: "gate", title: "Gate", icon: ArrowRight },
  { stage: "timing", title: "Timing check", icon: TrendingUp },
];

const emptyChain = () =>
  Object.fromEntries([...CHAIN.map((c) => c.stage), "narrator"].map((s) => [s, { status: "pending" }])) as Record<Stage, Node>;

const VERDICT_STYLE: Record<Verdict, { label: string; icon: typeof ShieldCheck; card: string; accent: string }> = {
  walk_away: {
    label: "Walk away",
    icon: OctagonX,
    card: "bg-coral/10 ring-coral/30",
    accent: "text-coral",
  },
  wait: {
    label: "Wait",
    icon: Timer,
    card: "bg-honey/15 ring-honey/40",
    accent: "text-honey-ink",
  },
  buy_now: {
    label: "Buy now",
    icon: CheckCircle2,
    card: "bg-mint/10 ring-mint/30",
    accent: "text-mint",
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
  const [result, setResult] = useState<DecisionResult | null>(null);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [price, setPrice] = useState("");
  const [market, setMarket] = useState<Market>("in");
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
      if (e.result) setResult(e.result);
      return;
    }
    if (e.stage === "verdict") {
      setFinal((f) => ({ ...f, ...e }));
      if (e.result) setResult(e.result);
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
    setResult(null);
    setBusy(true);
  }

  function analyze(q: string, m: Market = market) {
    if (!q.trim() || busy) return;
    reset();
    shotUrl.current = null;
    listen(`${API}/analyze/stream?q=${encodeURIComponent(q.trim())}&market=${m}`);
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

  async function analyzeImage(img: Picked, adPrice: string, m: Market = market) {
    if (busy) return;
    reset();
    shotUrl.current = img.url;
    try {
      const form = new FormData();
      // Demo screenshots go up byte-for-byte (cached); anything else is shrunk under 500 KB first.
      const body = img.demo ? img.blob : await compressForUpload(img.blob);
      form.append("file", body, body === img.blob ? img.name : "screenshot.jpg");
      if (adPrice.trim()) form.append("price", adPrice.trim());
      form.append("market", m);
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
    <>
      <SiteNav>
        <Link href="/#how" className={navLink}>
          How it works
        </Link>
        <AboutSheet />
      </SiteNav>
      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-8 px-4 pt-12 pb-16 sm:px-6 sm:pt-16">
        <header className="flex flex-col gap-2 animate-in fade-in slide-in-from-bottom-2 duration-500 motion-reduce:animate-none">
          <h1 className="font-display text-4xl font-bold tracking-[-0.03em] sm:text-5xl">Check a listing.</h1>
          <p className="text-ink-soft">Trust first, timing second.</p>
        </header>

        <section className="flex flex-col gap-3">
          <form
            className={cn(
              "flex flex-col gap-3 rounded-[28px] bg-card p-3 ring-1 ring-line transition-shadow focus-within:shadow-[0_20px_40px_-24px_rgb(23_21_31/0.3)] sm:p-4",
              dragging && "bg-iris-soft ring-2 ring-iris",
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
            <label htmlFor="q" className="sr-only">
              Paste a product link or describe the product — or drop, paste or pick a screenshot of an ad
            </label>
            <div className="flex flex-col gap-2 sm:flex-row">
              {picked ? (
                <div className="flex h-12 min-w-0 flex-1 items-center gap-2 rounded-full bg-paper px-2 text-sm">
                  {/* eslint-disable-next-line @next/next/no-img-element -- local object URL preview */}
                  <img src={picked.url} alt="" className="size-8 shrink-0 rounded-full object-cover" />
                  <span className="min-w-0 flex-1 truncate">{picked.name}</span>
                  <button
                    type="button"
                    aria-label="Remove screenshot"
                    onClick={() => {
                      URL.revokeObjectURL(picked.url);
                      setPicked(null);
                      setPrice("");
                    }}
                    className="rounded-full p-1.5 text-muted-foreground hover:bg-card"
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
                  placeholder="Paste a link, name a product, or drop an ad screenshot"
                  className="h-12 rounded-full border-0 bg-paper px-5 text-base"
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
                  className="size-12 rounded-full"
                  aria-label="Upload a screenshot"
                  title="Upload a screenshot"
                  onClick={() => fileInput.current?.click()}
                >
                  <ImagePlus />
                </Button>
                <Button
                  type="submit"
                  className="h-12 flex-1 rounded-full px-6 font-semibold hover:bg-iris hover:shadow-[0_12px_28px_-10px_rgb(109_90_230/0.8)] sm:flex-none"
                  disabled={busy || (!picked && !input.trim())}
                >
                  {busy ? <Loader2 className="animate-spin" /> : <>Analyze<ArrowRight /></>}
                </Button>
              </div>
            </div>
            {picked && (
              <div className="flex flex-col gap-1 px-2 sm:flex-row sm:items-center sm:gap-3">
                <label htmlFor="price" className="text-sm font-medium">
                  Price in the ad <span className="font-normal text-muted-foreground">(optional)</span>
                </label>
                <Input
                  id="price"
                  inputMode="decimal"
                  value={price}
                  onChange={(e) => setPrice(e.target.value.replace(/[^0-9.]/g, ""))}
                  placeholder={MARKETS.find((x) => x.code === market)!.symbol}
                  className="h-9 w-32 rounded-full bg-paper px-4"
                />
                <span className="text-xs text-muted-foreground">
                  Clarity doesn’t read prices from screenshots — type the price shown to have it checked.
                </span>
              </div>
            )}
            <MarketPicker market={market} onChange={setMarket} byLink={!picked && isLink(input)} disabled={busy} />
          </form>
          {demos.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">Try:</span>
              {demos.map((d) => (
                <button
                  key={d.key}
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setInput(d.url);
                    analyze(d.url, d.market);
                  }}
                  className="rounded-full bg-card px-3 py-1.5 ring-1 ring-line transition-colors hover:bg-iris-soft hover:text-iris hover:ring-iris/30 disabled:opacity-50"
                >
                  {d.label}
                  <span className="ml-1.5 text-xs text-muted-foreground">{d.market === "in" ? "India" : "US"}</span>
                </button>
              ))}
              {DEMO_SCREENSHOTS.map((shot) => (
                <button
                  key={shot.src}
                  type="button"
                  disabled={busy}
                  onClick={async () => {
                    const blob = await fetch(shot.src).then((r) => r.blob());
                    const img = pick(blob, shot.src.split("/").pop()!, true);
                    if (!img) return;
                    setPrice(shot.price);
                    setMarket(shot.market);
                    analyzeImage(img, shot.price, shot.market);
                  }}
                  className="inline-flex items-center gap-1.5 rounded-full bg-card px-3 py-1.5 ring-1 ring-line transition-colors hover:bg-iris-soft hover:text-iris hover:ring-iris/30 disabled:opacity-50"
                >
                  <ImagePlus className="size-3.5" />
                  {shot.label}
                  <span className="ml-0.5 text-xs text-muted-foreground">{shot.market === "in" ? "India" : "US"}</span>
                </button>
              ))}
            </div>
          )}
        </section>

        {error && (
          <div role="alert" data-testid="error" className="flex items-start gap-2 rounded-2xl bg-coral/10 p-4 text-sm ring-1 ring-coral/30">
            <XCircle className="mt-0.5 size-4 shrink-0 text-coral" />
            {error}
          </div>
        )}

        <section className="grid items-start gap-6 md:grid-cols-[1fr_1.1fr]">
          <ReasoningChain chain={chain} forensics={forensics} radar={radar} result={result} busy={busy} />
          <VerdictCard final={final} result={result} busy={busy} />
        </section>

        {result && final?.final_verdict && (
          <DecisionCharts r={result} successor={radar && hasSuccessor(radar) ? <SuccessorRadar l={radar} running={busy} bare /> : undefined} />
        )}

        {/* The chain's final section, shown after the verdict. Timing already scored its confirmed defects (wait-only, weight 0.10);
            an on-request live run comes later and never re-scores the verdict. */}
        {receipts && final?.final_verdict && <OwnerReceipts r={receipts} onRequest={requestReceipts} />}
      </main>
    </>
  );
}

function ReasoningChain({
  chain,
  forensics,
  radar,
  result,
  busy,
}: {
  chain: Record<Stage, Node>;
  forensics: Forensics | null;
  radar: Lifecycle | null;
  result: DecisionResult | null;
  busy: boolean;
}) {
  return (
    <div className="flex flex-col gap-1" aria-live="polite">
      <h2 className="mb-3 text-xl font-semibold tracking-tight">Reasoning</h2>
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
                    node.status === "done" ? "bg-iris/40" : "bg-line",
                  )}
                />
              )}
              <span
                className={cn(
                  "z-10 flex size-8 shrink-0 items-center justify-center rounded-full border bg-card transition-colors",
                  node.status === "running" && "border-iris bg-iris-soft text-iris",
                  node.status === "done" && !failed && !passed && "border-iris/40 text-iris",
                  node.status === "pending" && "opacity-40",
                  node.status === "skipped" && "border-dashed opacity-50",
                  failed && "border-coral bg-coral text-white",
                  passed && "border-mint bg-mint text-white",
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
                <StageBody stage={stage} node={node} result={result} />
                {stage === "trust" && forensics && node.status !== "pending" && !(result && isBoardCalm(forensics)) && <EvidenceBoard f={forensics} running={busy} />}
                {stage === "timing" && radar && node.status !== "pending" && node.status !== "skipped" && !(result && hasSuccessor(radar)) && (
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
        "rounded-full px-2 py-0.5 text-xs font-semibold",
        tone === "red" && "bg-coral/10 text-coral",
        tone === "green" && "bg-mint/10 text-mint",
        tone === "amber" && "bg-honey/10 text-honey-ink",
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
  if (stage === "timing") return tag(d.verdict === "buy" ? "buy now" : "wait", d.verdict === "buy" ? "green" : "amber");
  return null;
}

/** One line per step: status and its key number, linking to the chart that shows the evidence. */
function StageBody({ stage, node, result }: { stage: Stage; node: Node; result: DecisionResult | null }) {
  const d = node.data;
  if (node.status === "skipped") return null;
  if (!d) return null;
  if (node.status === "running") return <p className="text-sm text-muted-foreground">{d.message}</p>;
  if (stage === "resolve" && d.marketplace === "screenshot")
    return (
      <div className="flex flex-col gap-0.5 text-sm text-muted-foreground">
        <p>
          Identified from the screenshot: <span className="text-foreground">{d.identified_as}</span>
          {d.ad_price != null && <> · price in the ad {adPrice(d.ad_price, d.market?.code)}</>}
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
  const link = (href: string, text: ReactNode) => (
    <a href={href} className="text-sm text-muted-foreground underline decoration-foreground/20 underline-offset-2 hover:text-foreground">
      {text}
    </a>
  );
  if (stage === "trust" && result) {
    const t = result.trust.signals;
    const n = t.penalties.length;
    return link("#trust-checks", n ? `${n} check${n > 1 ? "s" : ""} failed · see checks ↓` : "See checks ↓");
  }
  if (stage === "timing" && result?.timing?.judged)
    return link("#timing", "See what drives it ↓");
  return null;
}

/** "Buy now — $298 is 9% below…" -> "$298 is 9% below…": the card's title already says the verdict. */
function reasonOnly(headline: string) {
  const rest = headline.replace(/^(buy now|wait|walk away|safe to buy)\s*[—–:-]\s*/i, "");
  return rest.charAt(0).toUpperCase() + rest.slice(1);
}

function VerdictCard({ final, result, busy }: { final: Evt | null; result: DecisionResult | null; busy: boolean }) {
  if (!final?.final_verdict)
    return (
      <div className="flex min-h-64 flex-col items-center justify-center gap-3 rounded-[28px] border border-dashed border-line p-6 text-center text-sm text-muted-foreground">
        <span className="flex gap-1.5">
          {(["bg-mint", "bg-honey", "bg-coral"] as const).map((c, i) => (
            <span key={c} className={cn("size-2 rounded-full", c, busy && "motion-safe:animate-pulse")} style={{ animationDelay: `${i * 200}ms` }} />
          ))}
        </span>
        {busy ? "Working through the checks…" : "Your verdict lands here."}
      </div>
    );

  const v = VERDICT_STYLE[final.final_verdict];
  const Icon = v.icon;
  return (
    <div className={cn("flex flex-col gap-5 rounded-[28px] p-6 ring-1 animate-in fade-in zoom-in-95 duration-500 sm:p-8", v.card)}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <div data-testid="verdict" className={cn("flex items-center gap-2 font-display text-4xl font-bold tracking-[-0.03em]", v.accent)}>
            <Icon className="size-7" />
            {final.timing_judged === false ? "Safe to buy — timing not judged" : v.label}
          </div>
          {final.headline && <p className="text-base">{reasonOnly(final.headline)}</p>}
        </div>
        {result && (result.final_verdict === "walk_away" || result.timing?.judged) ? (
          <div className="flex shrink-0 flex-col items-center rounded-2xl bg-card px-4 pt-3 pb-2.5 ring-1 ring-line animate-in fade-in zoom-in-95 duration-500">
            <VerdictGauge r={result} />
            <span className="text-xs text-muted-foreground">{result.final_verdict === "walk_away" ? "trust score" : "confidence"}</span>
          </div>
        ) : final.confidence != null && (
          <div className="flex shrink-0 flex-col items-end">
            <span className={cn("text-2xl font-semibold tabular-nums", v.accent)}>
              {Math.round(final.confidence * 100)}%
            </span>
            <span className="text-xs text-muted-foreground">confidence</span>
          </div>
        )}
      </div>

      {/* The narration lives in the charts below (DecisionCharts); the card keeps only what to do next. */}
      {final.market && (
        <p data-testid="market" className="-mt-2 text-xs text-muted-foreground">
          Checked against {final.market.name === "US" ? "US" : "Indian"} retailers · prices in {final.market.currency} (
          {final.market.symbol})
        </p>
      )}

      {final.wait_plan && <WhatToWaitFor p={final.wait_plan} />}

      {final.alternative && <BuyHereInstead a={final.alternative} />}
    </div>
  );
}

function BuyHereInstead({ a }: { a: Alternative }) {
  return (
    <div data-testid="alternative" className="flex flex-col gap-2 rounded-2xl bg-card/80 p-4 ring-1 ring-line">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Store className="size-4 text-mint" />
        Buy it here instead
      </div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="text-lg font-semibold">
          {a.store} · <span className="tabular-nums">{a.price_text}</span>
        </span>
        {a.saving_text && (
          <span className="text-sm text-mint tabular-nums">{a.saving_text} less</span>
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

function WhatToWaitFor({ p }: { p: WaitPlan }) {
  return (
    <div data-testid="wait-plan" className="flex flex-col gap-3 rounded-2xl bg-card/80 p-4 ring-1 ring-line">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Timer className="size-4 text-honey-ink" />
        What to wait for
      </div>
      {p.target && (
        <div className="flex gap-3">
          <Target className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div className="flex flex-col gap-0.5">
            <span className="text-lg font-semibold tabular-nums">{p.target.price_text} or less</span>
            <span className="text-sm text-muted-foreground first-letter:uppercase">{p.target.reason}</span>
          </div>
        </div>
      )}
      {p.window && (
        <div className="flex gap-3">
          <CalendarClock className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <span className="text-sm text-muted-foreground">{p.window.text}</span>
        </div>
      )}
    </div>
  );
}

/** India / US for typed text and screenshots. A pasted link decides its own market, so the picker steps aside. */
function MarketPicker({
  market,
  onChange,
  byLink,
  disabled,
}: {
  market: Market;
  onChange: (m: Market) => void;
  byLink: boolean;
  disabled: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-muted-foreground">Market</span>
      {byLink ? (
        <span className="text-xs text-muted-foreground">set by the link (amazon.in → India; amazon.com, eBay → US)</span>
      ) : (
        <div role="radiogroup" aria-label="Market" className="inline-flex rounded-full bg-card p-0.5 ring-1 ring-line">
          {MARKETS.map((m) => (
            <button
              key={m.code}
              type="button"
              role="radio"
              aria-checked={market === m.code}
              disabled={disabled}
              onClick={() => onChange(m.code)}
              className={cn(
                "rounded-full px-3 py-1 text-xs font-medium transition-colors disabled:opacity-50",
                market === m.code ? "bg-ink text-white" : "text-muted-foreground hover:bg-muted",
              )}
            >
              {m.label} ({m.symbol})
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
