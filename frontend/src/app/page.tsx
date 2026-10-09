import Link from "next/link";
import type { Metadata } from "next";
import {
  ArrowRight,
  Factory,
  Hourglass,
  Radar,
  ScanSearch,
  ShieldCheck,
  Store,
  Tag,
  TrendingUp,
  UsersRound,
  type LucideIcon,
} from "lucide-react";
import { AboutSheet } from "@/components/about-sheet";
import { HeroFlow } from "@/components/hero-flow";
import { navLink, pillCta, SiteNav } from "@/components/site-nav";
import { cn } from "@/lib/utils";

export const metadata: Metadata = {
  title: "Clarity — buy now, wait, or walk away",
  description:
    "Clarity checks whether a listing can be trusted, then whether now is the time to buy. Every reason is backed by live search evidence from 18 SerpApi engines.",
};

const REPO = "https://github.com/YashIIT0909/Clarity";

/** SerpApi engines, as Clarity calls them (backend/serpapi.py). Stage order matches the pipeline. */
const STAGES: { title: string; engines: { name: string; use: string }[] }[] = [
  {
    title: "Resolve",
    engines: [
      { name: "amazon_product", use: "Amazon listing details from an ASIN" },
      { name: "ebay_product", use: "eBay listing details from an item ID" },
      { name: "amazon", use: "A product name to the top Amazon result" },
      { name: "google_lens", use: "Names the product in an ad screenshot" },
    ],
  },
  {
    title: "Trust",
    engines: [
      { name: "google_shopping", use: "New-condition prices across stores" },
      { name: "google_immersive_product", use: "The full store price list" },
      { name: "ebay", use: "Used-price band and the seller's other listings" },
      { name: "bing_reverse_image", use: "Pages that already carry the listing photo" },
      { name: "google", use: "“<store> reviews” and “<store> scam”" },
      { name: "google_about_this_result", use: "What Google knows about the site" },
      { name: "google_ads_transparency_center", use: "Whether the store runs verified ads" },
    ],
  },
  {
    title: "Timing",
    engines: [
      { name: "google_trends", use: "5-year demand, seasons, and newer-model searches" },
      { name: "google_finance", use: "The maker's stock momentum" },
      { name: "google_autocomplete", use: "Suggestions that name a successor" },
      { name: "google_news", use: "Rumoured, announced or released successors" },
    ],
  },
  {
    title: "Owners",
    engines: [
      { name: "youtube", use: "Independent reviews of the exact model" },
      { name: "youtube_video_transcript", use: "Timestamped transcripts to quote from" },
      { name: "google_forums", use: "Owner threads, and forum talk about the store" },
    ],
  },
];

const TRUST: { icon: LucideIcon; title: string; text: string }[] = [
  { icon: Tag, title: "Price check", text: "Too cheap to be real?" },
  { icon: ScanSearch, title: "Photo forensics", text: "Is the photo lifted from elsewhere?" },
  { icon: Store, title: "Store reputation", text: "Reviews, scam reports, ad record." },
];

const TIMING: { icon: LucideIcon; title: string; w: number; waitOnly?: boolean }[] = [
  { icon: Tag, title: "Price position", w: 0.45 },
  { icon: TrendingUp, title: "Demand and season", w: 0.4 },
  { icon: Radar, title: "Successor Radar", w: 0.25, waitOnly: true },
  { icon: Factory, title: "Maker signal", w: 0.15 },
  { icon: UsersRound, title: "What owners say", w: 0.1, waitOnly: true },
];

function Verdict({ tone, children }: { tone: "mint" | "honey" | "coral"; children: string }) {
  const tones = {
    mint: "bg-mint/10 text-mint",
    honey: "bg-honey/15 text-[#a86f00]",
    coral: "bg-coral/10 text-coral",
  };
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold", tones[tone])}>
      <span className="size-1.5 rounded-full bg-current" />
      {children}
    </span>
  );
}

function Step({ n, icon: Icon, title, children, footer }: { n: string; icon: LucideIcon; title: string; children: React.ReactNode; footer: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-6 rounded-[28px] bg-white p-6 ring-1 ring-line transition-shadow duration-300 hover:shadow-[0_20px_40px_-24px_rgb(23_21_31/0.3)] sm:p-8">
      <div className="flex items-center gap-3">
        <span className="flex size-10 items-center justify-center rounded-xl bg-iris-soft text-iris">
          <Icon className="size-5" />
        </span>
        <h3 className="font-display text-xl font-semibold tracking-tight">{title}</h3>
        <span className="ml-auto font-mono text-xs text-ink-soft">{n}</span>
      </div>
      {children}
      <div className="mt-auto flex flex-wrap gap-2 border-t border-line pt-5">{footer}</div>
    </div>
  );
}

export default function Home() {
  const engineCount = new Set(STAGES.flatMap((s) => s.engines.map((e) => e.name))).size;
  const ctaClass =
    "group inline-flex h-11 items-center gap-2 rounded-full bg-ink px-5 text-sm font-semibold text-white transition-all hover:bg-iris hover:shadow-[0_12px_28px_-10px_rgb(109_90_230/0.8)]";

  return (
    <div className="flex flex-1 flex-col bg-paper font-body text-ink">
      <SiteNav>
        <a href="#how" className={navLink}>
          How it works
        </a>
        <a href="#serpapi" className={navLink}>
          SerpApi
        </a>
        <Link href="/check" className={pillCta}>
          Check a listing
        </Link>
      </SiteNav>

      <main className="mx-auto flex w-full max-w-5xl flex-col gap-28 px-4 pt-14 pb-24 sm:px-6 sm:pt-20">
        {/* Hero */}
        <section className="flex flex-col items-center gap-10 text-center">
          <HeroFlow engines={engineCount} />
          <div className="flex flex-col items-center gap-5 animate-in fade-in slide-in-from-bottom-3 fill-mode-both delay-300 duration-700 motion-reduce:animate-none">
            <h1 className="max-w-3xl font-display text-5xl leading-[0.95] font-bold tracking-[-0.035em] text-balance sm:text-7xl">
              Buy now, wait, or walk away.
            </h1>
            <p className="max-w-md text-lg text-ink-soft text-pretty">
              Trust first, timing second. Every reason links to the search it came from.
            </p>
            <div className="mt-2 flex flex-wrap items-center justify-center gap-3">
              <Link href="/check" className={ctaClass}>
                Check a listing
                <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" />
              </Link>
              <AboutSheet />
            </div>
          </div>
        </section>

        {/* How it decides */}
        <section id="how" className="flex scroll-mt-24 flex-col gap-10">
          <h2 className="max-w-xl font-display text-3xl font-semibold tracking-tight text-balance sm:text-4xl">
            A scam never gets a <span className="text-iris">“buy now”</span>.
          </h2>
          <div className="grid gap-4 md:grid-cols-2">
            <Step n="01" icon={ShieldCheck} title="Can I trust it?" footer={<><span className="text-sm text-ink-soft">Score under 60</span><ArrowRight className="size-4 self-center text-ink-soft" /><Verdict tone="coral">Walk away</Verdict></>}>
              <ul className="flex flex-col gap-4">
                {TRUST.map((t) => (
                  <li key={t.title} className="flex items-center gap-3">
                    <t.icon className="size-4 shrink-0 text-ink-soft" />
                    <span className="font-semibold whitespace-nowrap">{t.title}</span>
                    <span className="ml-auto text-right text-sm text-ink-soft">{t.text}</span>
                  </li>
                ))}
              </ul>
            </Step>
            <Step n="02" icon={Hourglass} title="Is now the time?" footer={<><Verdict tone="mint">Buy now</Verdict><Verdict tone="honey">Wait</Verdict></>}>
              <ul className="flex flex-col gap-3.5">
                {TIMING.map((t) => (
                  <li key={t.title} className="grid grid-cols-[1rem_8.5rem_1fr_2.25rem] items-center gap-3 text-sm">
                    <t.icon className="size-4 text-ink-soft" />
                    <span className="font-semibold">
                      {t.title}
                      {t.waitOnly && <span className="block text-xs font-normal text-ink-soft">wait only</span>}
                    </span>
                    <span className="h-1.5 rounded-full bg-paper">
                      <span className="block h-full rounded-full bg-iris" style={{ width: `${(t.w / 0.45) * 100}%` }} />
                    </span>
                    <span className="text-right font-mono text-xs tabular-nums text-ink-soft">{t.w.toFixed(2)}</span>
                  </li>
                ))}
              </ul>
            </Step>
          </div>
        </section>

        {/* SerpApi */}
        <section id="serpapi" className="grid scroll-mt-24 gap-10 md:grid-cols-[1fr_1.6fr] md:items-start">
          <div className="flex flex-col gap-3">
            <p className="font-display text-8xl leading-none font-bold tracking-tighter text-iris tabular-nums">{engineCount}</p>
            <h2 className="font-display text-3xl font-semibold tracking-tight">SerpApi engines.</h2>
            <p className="max-w-xs text-ink-soft">No scraping. Every signal is a structured result, cached and linked.</p>
          </div>
          <div className="flex flex-col divide-y divide-line rounded-[28px] bg-white px-6 ring-1 ring-line">
            {STAGES.map((s) => (
              <div key={s.title} className="flex flex-col gap-3 py-5 sm:flex-row sm:gap-6">
                <h3 className="w-20 shrink-0 pt-1 text-sm font-semibold">{s.title}</h3>
                <ul className="flex flex-wrap gap-1.5">
                  {s.engines.map((e) => (
                    <li
                      key={e.name}
                      title={e.use}
                      className="cursor-default rounded-full bg-paper px-2.5 py-1 font-mono text-[11px] text-ink transition-colors hover:bg-iris-soft hover:text-iris"
                    >
                      {e.name}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>

        {/* Claude */}
        <section className="relative flex flex-col items-start gap-5 overflow-hidden rounded-[32px] bg-ink p-8 text-white sm:p-12">
          <div className="pointer-events-none absolute -top-24 -right-24 size-72 rounded-full bg-iris/40 blur-3xl" />
          <h2 className="relative max-w-lg font-display text-3xl font-semibold tracking-tight text-balance sm:text-4xl">
            Let Claude check before it recommends.
          </h2>
          <p className="relative max-w-md text-white/65">
            One read-only MCP tool, <code className="font-mono text-sm text-white">clarity_check_listing</code>, with the same verdict and evidence.
          </p>
          <div className="relative text-ink">
            <AboutSheet />
          </div>
        </section>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex w-full max-w-5xl flex-wrap items-center justify-between gap-3 px-4 py-6 text-sm text-ink-soft sm:px-6">
          <span>
            Clarity · MIT · data by{" "}
            <a href="https://serpapi.com" className="underline decoration-ink/20 underline-offset-2 hover:text-ink">
              SerpApi
            </a>
          </span>
          <a href={REPO} className="hover:text-ink">
            GitHub
          </a>
        </div>
      </footer>
    </div>
  );
}
