"use client";

import { useState, type ReactNode } from "react";
import { Check, Copy, Info, OctagonX, ShieldQuestion, Timer, CheckCircle2, CircleHelp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

// The backend serves the MCP server at exactly /mcp (Streamable HTTP), next to the app's own API.
const MCP_URL = `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/mcp`;

const VERDICTS = [
  { name: "walk_away", text: "Failed the trust check. Timing isn't checked.", icon: OctagonX, tone: "text-coral" },
  { name: "buy_now", text: "Trusted, and timing favours buying now.", icon: CheckCircle2, tone: "text-mint" },
  { name: "wait", text: "Trusted, but timing favours waiting.", icon: Timer, tone: "text-honey-ink" },
  { name: "buy_timing_not_judged", text: "Trusted; not enough data to judge timing.", icon: CircleHelp, tone: "text-muted-foreground" },
  { name: "could_not_check", text: "The check didn't finish. Unverified, not safe.", icon: ShieldQuestion, tone: "text-muted-foreground" },
];

function CopyLine({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <div className="flex items-center gap-1 rounded-lg border bg-muted/50 py-1 pr-1 pl-3">
        <code className="min-w-0 flex-1 overflow-x-auto font-mono text-xs whitespace-nowrap">{value}</code>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`Copy ${label.toLowerCase()}`}
          onClick={() => {
            navigator.clipboard?.writeText(value).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            });
          }}
        >
          {copied ? <Check /> : <Copy />}
        </Button>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-medium">{title}</h3>
      {children}
    </section>
  );
}

export function AboutSheet() {
  return (
    <Sheet>
      <SheetTrigger render={<Button variant="outline" size="sm" className="rounded-full px-3" />}>
        <Info data-icon="inline-start" />
        About / Add to Claude
      </SheetTrigger>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader className="pr-12">
          <SheetTitle>Clarity in Claude</SheetTitle>
          <SheetDescription>
            The same check this page runs is also an MCP server. Add it to Claude and it can check a listing before it
            recommends one — with the Evidence Board shown right in the chat.
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-6 px-4 pb-6 text-sm">
          <Section title="Add it to Claude">
            <p className="text-muted-foreground">
              In Claude, open <span className="text-foreground">Settings → Connectors → Add custom connector</span> and
              paste the server URL.
            </p>
            <CopyLine label="Server URL" value={MCP_URL} />
            <p className="text-muted-foreground">Or, in Claude Code:</p>
            <CopyLine label="Command" value={`claude mcp add --transport http clarity ${MCP_URL}`} />
          </Section>

          <Section title="What Claude gets">
            <p className="text-muted-foreground">
              One tool, <code className="font-mono text-xs text-foreground">clarity_check_listing</code>. Give it an
              Amazon or eBay URL (or a product description) and it runs the trust check first — price against the
              market, seller record, returns, photo forensics — and only checks timing for a listing that passes.
            </p>
            <p className="text-muted-foreground">
              It’s read-only: Clarity never buys anything or touches an account. Clients that support MCP Apps render
              the Evidence Board inline, including “What owners say”.
            </p>
          </Section>

          <Section title="Verdicts it returns">
            <ul className="flex flex-col gap-2">
              {VERDICTS.map((v) => (
                <li key={v.name} className="flex items-start gap-2">
                  <v.icon className={`mt-0.5 size-4 shrink-0 ${v.tone}`} />
                  <div className="flex flex-col">
                    <code className="font-mono text-xs">{v.name}</code>
                    <span className="text-muted-foreground">{v.text}</span>
                  </div>
                </li>
              ))}
            </ul>
          </Section>
        </div>
      </SheetContent>
    </Sheet>
  );
}
