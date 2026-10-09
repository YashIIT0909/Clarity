import { CheckCircle2, ImageUp, Link2, OctagonX, Search, ShieldCheck, Timer, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/*
 * The landing hero: what goes into a check, Clarity in the middle, and the three verdicts out.
 * Everything is laid out in a 1000×400 box; tiles are sized in container units so the picture
 * scales as one piece. Moving dots are SMIL and hidden for reduced motion.
 */

type Node = { x: number; y: number; s: number; icon: LucideIcon; label: string; tone: string; delay: number };

const INPUTS: (Node & { path: string; knee?: [number, number] })[] = [
  { x: 190, y: 90, s: 80, icon: Link2, label: "Product link", tone: "plain", delay: 100, path: "M230 90 H285 L360 200 H436", knee: [285, 90] },
  { x: 85, y: 200, s: 104, icon: ImageUp, label: "Ad screenshot", tone: "plain", delay: 0, path: "M137 200 H436" },
  { x: 210, y: 315, s: 76, icon: Search, label: "Product name", tone: "plain", delay: 200, path: "M248 315 H285 L360 200 H436", knee: [285, 315] },
];

const OUTPUTS: (Node & { path: string; knee?: [number, number]; dot: string })[] = [
  { x: 800, y: 90, s: 84, icon: CheckCircle2, label: "Buy now", tone: "mint", delay: 500, dot: "var(--color-mint)", path: "M564 200 H640 L715 90 H758", knee: [715, 90] },
  { x: 905, y: 200, s: 100, icon: Timer, label: "Wait", tone: "honey", delay: 400, dot: "var(--color-honey)", path: "M564 200 H855" },
  { x: 790, y: 315, s: 76, icon: OctagonX, label: "Walk away", tone: "coral", delay: 600, dot: "var(--color-coral)", path: "M564 200 H640 L715 315 H752", knee: [715, 315] },
];

const TONES: Record<string, string> = {
  plain: "bg-white text-ink ring-1 ring-line shadow-[0_10px_30px_-12px_rgb(23_21_31/0.25)]",
  iris: "bg-gradient-to-b from-[#8a7af0] to-iris text-white shadow-[0_24px_50px_-16px_rgb(109_90_230/0.7),inset_0_1px_0_rgb(255_255_255/0.35)]",
  mint: "bg-gradient-to-b from-[#3cc48f] to-mint text-white shadow-[0_16px_34px_-14px_rgb(31_168_116/0.75),inset_0_1px_0_rgb(255_255_255/0.35)]",
  honey: "bg-gradient-to-b from-[#ffc24d] to-honey text-white shadow-[0_16px_34px_-14px_rgb(242_165_22/0.75),inset_0_1px_0_rgb(255_255_255/0.4)]",
  coral: "bg-gradient-to-b from-[#ff8670] to-coral text-white shadow-[0_16px_34px_-14px_rgb(240_96_74/0.75),inset_0_1px_0_rgb(255_255_255/0.35)]",
};

const cq = (v: number) => `${v / 10}cqw`;

function Tile({ x, y, s, icon: Icon, label, tone, delay, children }: Node & { children?: React.ReactNode }) {
  return (
    <div
      className="absolute animate-in fade-in zoom-in-75 fill-mode-both duration-700 ease-out motion-reduce:animate-none"
      style={{ left: cq(x - s / 2), top: cq(y - s / 2), width: cq(s), animationDelay: `${delay}ms` }}
    >
      <div className="motion-safe:animate-float" style={{ animationDelay: `${-delay * 3}ms` }}>
        {children}
        <div className={cn("relative flex aspect-square items-center justify-center rounded-[26%]", TONES[tone])}>
          <Icon className="size-[42%]" strokeWidth={1.75} />
        </div>
        <p className="absolute inset-x-[-50%] top-full mt-[1cqw] hidden text-center text-[clamp(10px,1.25cqw,13px)] font-semibold text-ink-soft sm:block">
          {label}
        </p>
      </div>
    </div>
  );
}

// One cycle: all inputs travel into Clarity together, then all verdict dots leave together.
const CYCLE = 4.4;
const START = 1;

function Dot({ path, color, phase }: { path: string; color: string; phase: "in" | "out" }) {
  const motion = phase === "in" ? { keyPoints: "0;1;1", splines: "0.45 0 0.55 1;0 0 1 1" } : { keyPoints: "0;0;1", splines: "0 0 1 1;0.45 0 0.55 1" };
  const fade = phase === "in" ? { values: "0;1;1;0;0", keyTimes: "0;0.06;0.44;0.5;1" } : { values: "0;0;1;1;0", keyTimes: "0;0.5;0.56;0.94;1" };
  return (
    <circle r="4" fill={color} opacity="0">
      <animateMotion path={path} dur={`${CYCLE}s`} begin={`${START}s`} repeatCount="indefinite" calcMode="spline" keyPoints={motion.keyPoints} keyTimes="0;0.5;1" keySplines={motion.splines} />
      <animate attributeName="opacity" values={fade.values} keyTimes={fade.keyTimes} dur={`${CYCLE}s`} begin={`${START}s`} repeatCount="indefinite" />
    </circle>
  );
}

export function HeroFlow({ engines }: { engines: number }) {
  return (
    <div aria-hidden className="relative w-full [container-type:inline-size]">
      <div className="relative aspect-[1000/400] w-full">
        <svg viewBox="0 0 1000 400" className="absolute inset-0 size-full" fill="none">
          <g stroke="var(--color-line)" strokeWidth="1.5">
            {[...INPUTS, ...OUTPUTS].map((n) => (
              <path key={n.label} d={n.path} />
            ))}
          </g>
          {[...INPUTS, ...OUTPUTS].map((n) =>
            n.knee ? <circle key={n.label} cx={n.knee[0]} cy={n.knee[1]} r="4" fill="var(--color-iris)" opacity="0.55" /> : null,
          )}
          <g className="motion-reduce:hidden">
            {INPUTS.map((n) => (
              <Dot key={n.label} path={n.path} color="var(--color-iris)" phase="in" />
            ))}
            {OUTPUTS.map((n) => (
              <Dot key={n.label} path={n.path} color={n.dot} phase="out" />
            ))}
          </g>
        </svg>

        {INPUTS.map((n) => (
          <Tile key={n.label} {...n} />
        ))}

        <Tile x={500} y={200} s={128} icon={ShieldCheck} label={`${engines} SerpApi engines`} tone="iris" delay={250}>
          <span className="absolute inset-0 rounded-[26%] bg-iris motion-safe:animate-halo" style={{ animationDuration: `${CYCLE}s`, animationDelay: `${START + CYCLE / 2}s` }} />
        </Tile>

        {OUTPUTS.map((n) => (
          <Tile key={n.label} {...n} />
        ))}
      </div>
    </div>
  );
}
