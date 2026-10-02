import { useEffect, useState, type ReactNode } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CirclePlay,
  CloudOff,
  Gauge,
  Loader2,
  MessageSquareWarning,
  MessagesSquare,
  RotateCcw,
  SearchCheck,
  ShieldQuestion,
  ThumbsUp,
  TriangleAlert,
  UsersRound,
  X,
} from "lucide-react";
import { cn } from "./cn";

type EngineState = { status: "idle" | "running" | "done" | "unavailable"; found?: number; ms?: number };
type VideoSrc = { type: "video"; channel: string; video_id: string; timestamp_s: number; quote: string; link: string; model?: string };
type ForumSrc = { type: "forum"; title: string; link: string; source: string | null; date: string | null };
type Src = VideoSrc | ForumSrc;
type ItemType = "problem" | "concern" | "criticism" | "praise";
export type ReceiptCard = {
  kind: "issue" | "praise";
  type?: ItemType;
  label: string;
  label_display?: string;
  source_count: number;
  sources: Src[];
  marketplace?: { site: string; title: string; sentiment: string }[];
  thread_count?: number;
  sites?: number;
};
type Video = { video_id: string; title: string; channel: string; link: string };
type Summary = {
  status: "found" | "none" | "unavailable" | "on_request";
  query_id?: string;
  why?: string;
  note?: string;
  retry?: boolean;
  known_issues: ReceiptCard[];
  known_praise: ReceiptCard[];
  forum_reported: ReceiptCard[];
  mentioned_once: ReceiptCard[];
};

export type Receipts = {
  engines: { youtube: EngineState; transcripts: EngineState & { done: number; total?: number }; forums: EngineState };
  videos?: Video[];
  points: Record<string, number>;
  summary?: Summary;
  story?: { text: string; fallback?: boolean };
  queryId?: string;
  run?: { state: "running" | "failed"; startedAt: number; message?: string }; // the on-request live run
};

/** The SSE "receipts" and "owners" events (after the verdict, or from /receipts/stream/{id}), folded into card state. */
export type ReceiptsEvent = { stage: "receipts" | "owners"; type?: string; [k: string]: unknown };

const idle = (): Receipts["engines"] => ({ youtube: { status: "idle" }, transcripts: { status: "idle", done: 0 }, forums: { status: "idle" } });

export function reduceReceipts(r: Receipts | null, e: ReceiptsEvent): Receipts {
  const base: Receipts = r ?? { engines: idle(), points: {} };
  if (e.stage === "owners") return { ...base, story: { text: e.text as string, fallback: e.fallback as boolean } };
  if (e.type === "start")
    return {
      ...base,
      engines: { youtube: { status: "running" }, transcripts: { status: "running", done: 0 }, forums: { status: "running" } },
      videos: undefined,
      points: {},
      summary: undefined,
      story: undefined,
    };
  if (e.type === "engine_done") {
    const s: EngineState = { status: e.ok ? "done" : "unavailable", found: e.found as number, ms: e.ms as number };
    if (e.engine === "youtube") return { ...base, engines: { ...base.engines, youtube: s } };
    if (e.engine === "google_forums") return { ...base, engines: { ...base.engines, forums: s } };
    const t = base.engines.transcripts;
    const done = t.done + 1;
    const finished = t.total != null && done >= t.total;
    return { ...base, engines: { ...base.engines, transcripts: { ...t, done, status: finished ? "done" : "running" } } };
  }
  if (e.type === "videos") {
    const videos = e.picked as Video[];
    const t = base.engines.transcripts;
    return {
      ...base,
      videos,
      engines: { ...base.engines, transcripts: { ...t, total: videos.length, status: videos.length && t.done < videos.length ? "running" : "done" } },
    };
  }
  if (e.type === "points") return { ...base, points: { ...base.points, [e.video_id as string]: (e.points as unknown[]).length } };
  if (e.type === "summary") {
    const s = e as unknown as Summary;
    const settle = (x: EngineState): EngineState => (x.status === "running" ? { ...x, status: "done" } : x);
    return {
      ...base,
      summary: s,
      queryId: s.query_id,
      run: undefined,
      engines: { youtube: settle(base.engines.youtube), transcripts: { ...base.engines.transcripts, ...settle(base.engines.transcripts) }, forums: settle(base.engines.forums) },
    };
  }
  if (e.type === "error") return { ...base, run: { state: "failed", startedAt: base.run?.startedAt ?? Date.now(), message: e.message as string } };
  return base;
}

/** The on-request live run begins: engines dim until its "start" event arrives. */
export const startLiveRun = (r: Receipts): Receipts => ({ ...r, engines: idle(), run: { state: "running", startedAt: Date.now() } });
export const failLiveRun = (r: Receipts, message: string): Receipts => ({ ...r, run: { state: "failed", startedAt: r.run?.startedAt ?? Date.now(), message } });

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const day = (d: string | null) => d ?? null;
const show = (c: ReceiptCard) => c.label_display ?? c.label;
const typeOf = (c: ReceiptCard): ItemType => c.type ?? (c.kind === "praise" ? "praise" : "criticism");

const TYPE_STYLE: Record<ItemType, { chip: string; on: string; icon: typeof ThumbsUp; tag?: string }> = {
  problem: {
    chip: "border-red-500/40 bg-red-500/5 text-red-800 dark:text-red-300",
    on: "ring-2 ring-red-500/40",
    icon: TriangleAlert,
    tag: "problem",
  },
  concern: {
    chip: "border-dashed border-amber-500/70 bg-transparent text-amber-900 dark:text-amber-200",
    on: "ring-2 ring-amber-500/30",
    icon: ShieldQuestion,
    tag: "concern raised by reviewers",
  },
  criticism: {
    chip: "border-amber-500/40 bg-amber-500/5 text-amber-900 dark:text-amber-200",
    on: "ring-2 ring-amber-500/40",
    icon: MessageSquareWarning,
    tag: "common criticism",
  },
  praise: {
    chip: "border-emerald-500/40 bg-emerald-500/5 text-emerald-800 dark:text-emerald-300",
    on: "ring-2 ring-emerald-500/40",
    icon: ThumbsUp,
  },
};

export function OwnerReceipts({ r, onRequest }: { r: Receipts; onRequest: () => void }) {
  const s = r.summary;
  const live = r.run?.state === "running";
  const failed = r.run?.state === "failed";
  return (
    <section data-testid="owner-receipts" className="flex min-w-0 flex-col gap-3 rounded-xl border bg-muted/20 p-4 animate-in fade-in sm:p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="flex items-center gap-2 text-sm font-medium uppercase tracking-wide text-muted-foreground">
          <UsersRound className="size-4" />
          What owners say
        </h2>
        <span className="text-xs text-muted-foreground">Independent reviews and forums · context only, not part of the verdict</span>
      </div>

      {(r.engines.youtube.status !== "idle" || live) && <EngineStrip r={r} live={live} />}
      {live && <LiveProgress r={r} />}

      {failed ? (
        <Calm
          testid="receipts-retry"
          icon={RotateCcw}
          tone="muted"
          text={
            // MCP view: the tool returns after 45 s while the live run carries on in the background.
            r.run?.message?.startsWith("still")
              ? "Still checking review videos and forums. Ask again in a minute: the run carries on and keeps its progress."
              : "The live check was interrupted before it finished. Nothing was lost — you can start it again."
          }
        >
          <RequestButton onRequest={onRequest} label="Try again" />
        </Calm>
      ) : !s || live ? null : s.status === "on_request" ? (
        <OnRequest onRequest={onRequest} />
      ) : s.status === "none" ? (
        <Calm testid="receipts-none" icon={CheckCircle2} tone="green" text={r.story?.text ?? "Independent reviews don't show a recurring problem."} />
      ) : s.status === "unavailable" ? (
        <Unavailable s={s} story={r.story?.text} onRequest={onRequest} />
      ) : (
        <Found s={s} story={r.story?.text} />
      )}

      {s && !live && !failed && s.status !== "on_request" && s.mentioned_once?.length > 0 && <MentionedOnce cards={s.mentioned_once} />}
    </section>
  );
}

function EngineStrip({ r, live }: { r: Receipts; live: boolean }) {
  const t = r.engines.transcripts;
  const rows: [string, EngineState, string | null][] = [
    ["YouTube", r.engines.youtube, r.engines.youtube.found != null ? `${r.engines.youtube.found}` : null],
    ["Transcripts", t, t.total != null ? `${t.done}/${t.total}` : null],
    ["Forums", r.engines.forums, r.engines.forums.found != null ? `${r.engines.forums.found}` : null],
  ];
  const done = rows.filter(([, e]) => e.status === "done").length;
  return (
    <div data-testid="receipts-engines" className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
      {rows.map(([name, e, count]) => (
        <span
          key={name}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 transition-colors duration-500",
            (e.status === "running" || e.status === "idle") && "text-muted-foreground",
            e.status === "idle" && "opacity-50",
            e.status === "done" && "border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
            e.status === "unavailable" && "border-dashed text-muted-foreground line-through decoration-1",
          )}
        >
          {e.status === "running" ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <span className={cn("size-1.5 rounded-full", e.status === "done" ? "bg-emerald-500" : "bg-zinc-400")} />
          )}
          {name}
          {count && e.status !== "unavailable" && <span className="tabular-nums opacity-70">{count}</span>}
        </span>
      ))}
      <span className="ml-auto tabular-nums text-muted-foreground">{live ? "checking reviews and forums…" : `${done} of 3 sources`}</span>
    </div>
  );
}

function LiveProgress({ r }: { r: Receipts }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const elapsed = Math.max(0, Math.round((now - (r.run?.startedAt ?? now)) / 1000));
  const step = !r.videos
    ? "Searching YouTube for reviews of this model…"
    : r.engines.transcripts.status === "running"
      ? "Reading the transcripts…"
      : "Pulling out what reviewers and owners agree on…";
  return (
    <div data-testid="receipts-live" className="flex min-w-0 flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          {step}
        </span>
        <span className="text-xs tabular-nums text-muted-foreground">
          {mmss(elapsed)} elapsed · usually about 2–3 minutes
        </span>
      </div>
      {r.videos && r.videos.length > 0 && (
        <ul className="grid min-w-0 gap-2 sm:grid-cols-3">
          {r.videos.map((v) => (
            <li key={v.video_id} className="flex min-w-0 items-center gap-2 rounded-md border bg-background p-1.5 text-xs animate-in fade-in">
              {/* eslint-disable-next-line @next/next/no-img-element -- YouTube's own thumbnail */}
              <img src={`https://i.ytimg.com/vi/${v.video_id}/mqdefault.jpg`} alt="" className="h-9 w-16 shrink-0 rounded object-cover" />
              <span className="min-w-0">
                <span className="block truncate font-medium">{v.channel}</span>
                <span className="block truncate text-muted-foreground">
                  {r.points[v.video_id] != null ? `${r.points[v.video_id]} points checked` : "reading…"}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RequestButton({ onRequest, label }: { onRequest: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onRequest}
      className="inline-flex shrink-0 items-center gap-1.5 rounded-md border bg-background px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted"
    >
      <SearchCheck className="size-4" />
      {label}
    </button>
  );
}

function OnRequest({ onRequest }: { onRequest: () => void }) {
  return (
    <div data-testid="receipts-on-request" className="flex flex-col gap-3 rounded-lg border border-dashed p-3 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-sm text-muted-foreground">
        Owner reviews haven’t been checked for this product yet. Clarity can search review videos and forums live and pull out
        what keeps coming up.
        <span className="block text-xs">Takes about 2–3 minutes · your verdict above doesn’t change.</span>
      </p>
      <RequestButton onRequest={onRequest} label="See what owners say" />
    </div>
  );
}

function Calm({
  icon: Icon,
  tone,
  text,
  testid,
  children,
}: {
  icon: typeof CheckCircle2;
  tone: "green" | "muted" | "amber" | "sky";
  text: string;
  testid: string;
  children?: ReactNode;
}) {
  return (
    <div
      data-testid={testid}
      className={cn(
        "flex flex-col gap-3 rounded-md px-3 py-2.5 text-sm sm:flex-row sm:items-center sm:justify-between",
        tone === "green" && "bg-emerald-500/5 text-foreground",
        tone === "muted" && "bg-muted/60 text-muted-foreground",
        tone === "amber" && "bg-amber-500/5 text-foreground",
        tone === "sky" && "bg-sky-500/5 text-foreground",
      )}
    >
      <span className="flex min-w-0 items-start gap-2">
        <Icon
          className={cn(
            "mt-0.5 size-4 shrink-0",
            tone === "green" && "text-emerald-600",
            tone === "amber" && "text-amber-600",
            tone === "sky" && "text-sky-600",
          )}
        />
        <span className="min-w-0">{text}</span>
      </span>
      {children}
    </div>
  );
}

function Unavailable({ s, story, onRequest }: { s: Summary; story?: string; onRequest: () => void }) {
  const why = s.why ?? "";
  if (why.includes("free-tier limit"))
    return (
      <Calm testid="receipts-limit" icon={Gauge} tone="amber" text={story ?? "What owners say isn't available right now: Groq's free-tier limit was reached. Try again in a while."}>
        <RequestButton onRequest={onRequest} label="Try again" />
      </Calm>
    );
  if (why.includes("couldn't be reached"))
    return (
      <Calm testid="receipts-down" icon={CloudOff} tone="sky" text={story ?? "What owners say isn't available right now: the language model couldn't be reached. Try again in a while."}>
        <RequestButton onRequest={onRequest} label="Try again" />
      </Calm>
    );
  return <Calm testid="receipts-unavailable" icon={UsersRound} tone="muted" text={story ?? `We couldn't check what owners say: ${why}.`} />;
}

function Found({ s, story }: { s: Summary; story?: string }) {
  return (
    <div data-testid="receipts-found" className="flex min-w-0 flex-col gap-3">
      {story && <p className="text-sm leading-relaxed">{story}</p>}
      {s.known_issues.length > 0 && <Chips title="Known issues" cards={s.known_issues} />}
      {s.known_praise.length > 0 && <Chips title="Known praise" cards={s.known_praise} />}
      {s.forum_reported?.length > 0 && <Chips title="Reported by owners in forums" cards={s.forum_reported} forum />}
    </div>
  );
}

function Chips({ title, cards, forum }: { title: string; cards: ReceiptCard[]; forum?: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  const selected = cards.find((c) => c.label === open);
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <span className="text-xs uppercase tracking-wide text-muted-foreground">{title}</span>
      <div className="flex min-w-0 flex-wrap gap-1.5">
        {cards.map((c) => {
          const st = TYPE_STYLE[typeOf(c)];
          const Icon = forum ? MessagesSquare : st.icon;
          const n = forum ? c.thread_count ?? c.source_count : c.source_count;
          return (
            <button
              key={c.label}
              type="button"
              data-testid="receipt-chip"
              data-type={forum ? "forum" : typeOf(c)}
              aria-expanded={open === c.label}
              onClick={() => setOpen(open === c.label ? null : c.label)}
              className={cn(
                "inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-sm transition-shadow",
                forum ? "border-sky-500/40 bg-sky-500/5 text-sky-900 dark:text-sky-200" : st.chip,
                open === c.label && (forum ? "ring-2 ring-sky-500/40" : st.on),
              )}
            >
              <Icon className="size-3.5 shrink-0" />
              <span className="truncate">{show(c)}</span>
              <span className="shrink-0 tabular-nums opacity-70">
                · {n} {forum ? "threads" : n === 1 ? "source" : "sources"}
              </span>
              {!forum && st.tag && <span className="hidden shrink-0 rounded bg-foreground/5 px-1 text-[10px] uppercase tracking-wide opacity-80 sm:inline">{st.tag}</span>}
            </button>
          );
        })}
      </div>
      {selected && <Sources c={selected} forum={forum} />}
    </div>
  );
}

function Sources({ c, forum }: { c: ReceiptCard; forum?: boolean }) {
  const [playing, setPlaying] = useState<string | null>(null);
  const seen = new Set<string>();
  const rows = c.sources.filter((x) => {
    const k = x.type === "video" ? `${x.video_id}@${x.timestamp_s}` : x.link;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  return (
    <div data-testid="receipt-sources" className="flex min-w-0 flex-col gap-2 rounded-lg border bg-background p-2.5 animate-in fade-in">
      <p className="text-xs text-muted-foreground">
        {forum
          ? "Reported by owners in forums: "
          : { criticism: "A common criticism: ", problem: "A recurring problem: ", concern: "A possible risk reviewers flag (not reported as happening): ", praise: "Praised by: " }[typeOf(c)]}
        <span className="text-foreground">{show(c)}</span>
      </p>
      <ul className="flex min-w-0 flex-col gap-2">
        {rows.map((x) =>
          x.type === "video" ? (
            <VideoRow key={`${x.video_id}@${x.timestamp_s}`} x={x} playing={playing === `${x.video_id}@${x.timestamp_s}`}
              onToggle={() => setPlaying(playing === `${x.video_id}@${x.timestamp_s}` ? null : `${x.video_id}@${x.timestamp_s}`)} />
          ) : (
            <li key={x.link} className="flex min-w-0 gap-2 p-1 text-sm">
              <MessagesSquare className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0">
                <a href={x.link} target="_blank" rel="noopener noreferrer nofollow" className="break-words underline decoration-foreground/30 underline-offset-2 hover:decoration-foreground">
                  {x.title}
                </a>
                <span className="text-xs text-muted-foreground">
                  {" "}
                  · {x.source}
                  {day(x.date) && ` · ${day(x.date)}`}
                </span>
              </span>
            </li>
          ),
        )}
      </ul>
      {c.marketplace && c.marketplace.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Also in Amazon’s review summary: {c.marketplace.map((m) => `${m.title} (${m.sentiment})`).join(", ")} · not counted as a source
        </p>
      )}
    </div>
  );
}

/** One video source. A removed or private video gets YouTube's 120×90 placeholder thumbnail (a live one is 320×180):
 *  then the row degrades to the quote and a plain link, never a broken player. */
function VideoRow({ x, playing, onToggle }: { x: VideoSrc; playing: boolean; onToggle: () => void }) {
  const [gone, setGone] = useState(false);
  const at = `${x.channel} at ${mmss(x.timestamp_s)}`;
  if (gone)
    return (
      <li data-testid="video-gone" className="flex min-w-0 flex-col gap-0.5 rounded-md p-1 text-sm">
        <span>
          <span className="font-medium">{x.channel}</span>
          <span className="text-muted-foreground"> at {mmss(x.timestamp_s)} · this video is no longer available to play here</span>
        </span>
        <span className="italic text-muted-foreground">“{x.quote}”</span>
        <a href={`https://www.youtube.com/watch?v=${x.video_id}&t=${x.timestamp_s}s`} target="_blank" rel="noopener noreferrer nofollow" className="w-fit text-xs underline decoration-foreground/30 underline-offset-2 hover:decoration-foreground">
          Watch on YouTube at {mmss(x.timestamp_s)}
        </a>
      </li>
    );
  return (
    <li className="flex min-w-0 flex-col gap-2">
      <button type="button" data-testid="video-source" onClick={onToggle} className="flex min-w-0 items-start gap-2.5 rounded-md p-1 text-left transition-colors hover:bg-muted/60">
        <span className="relative shrink-0">
          {/* eslint-disable-next-line @next/next/no-img-element -- YouTube's own thumbnail */}
          <img
            src={`https://i.ytimg.com/vi/${x.video_id}/mqdefault.jpg`}
            alt=""
            className="h-[54px] w-24 rounded object-cover"
            onLoad={(e) => e.currentTarget.naturalWidth === 120 && setGone(true)}
            onError={() => setGone(true)}
          />
          <CirclePlay className="absolute inset-0 m-auto size-6 text-white drop-shadow" />
          <span className="absolute bottom-0.5 right-0.5 rounded bg-black/75 px-1 text-[10px] font-medium tabular-nums text-white">{mmss(x.timestamp_s)}</span>
        </span>
        <span className="min-w-0 text-sm">
          <span className="font-medium">{x.channel}</span>
          <span className="text-muted-foreground"> at {mmss(x.timestamp_s)}</span>
          {x.model === "not confirmed" && (
            <span className="ml-1.5 rounded bg-muted px-1 text-[10px] uppercase tracking-wide text-muted-foreground">model not confirmed</span>
          )}
          <span className="mt-0.5 block italic text-muted-foreground">“{x.quote}”</span>
        </span>
      </button>
      {playing && (
        <div data-testid="video-player" className="relative w-full min-w-0 overflow-hidden rounded-md bg-black">
          <iframe
            className="aspect-video w-full"
            src={`https://www.youtube-nocookie.com/embed/${x.video_id}?start=${x.timestamp_s}&autoplay=1&rel=0`}
            title={at}
            allow="autoplay; encrypted-media; picture-in-picture"
            allowFullScreen
          />
          <button type="button" aria-label="Close the video" onClick={onToggle} className="absolute right-1.5 top-1.5 rounded-full bg-black/60 p-1 text-white hover:bg-black/80">
            <X className="size-3.5" />
          </button>
        </div>
      )}
    </li>
  );
}

function MentionedOnce({ cards }: { cards: ReceiptCard[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <button
        type="button"
        data-testid="mentioned-once-toggle"
        onClick={() => setOpen(!open)}
        className="inline-flex w-fit items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        Mentioned once ({cards.length}) · one source each, not a pattern
      </button>
      {open && <Chips title="Mentioned once" cards={cards} />}
    </div>
  );
}
