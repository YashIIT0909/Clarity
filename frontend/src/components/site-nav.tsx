import type { ReactNode } from "react";
import Link from "next/link";

/** The floating pill nav shared by the landing and check pages. */
export function SiteNav({ children }: { children: ReactNode }) {
  return (
    <nav className="sticky top-4 z-40 mx-auto mt-4 flex w-[calc(100%-2rem)] max-w-2xl items-center justify-between gap-3 rounded-full bg-white/80 py-1.5 pr-1.5 pl-5 ring-1 ring-line backdrop-blur">
      <Link href="/" className="font-display text-lg font-bold tracking-tight">
        Clarity
      </Link>
      <div className="flex items-center gap-1">{children}</div>
    </nav>
  );
}

export const navLink = "hidden rounded-full px-3 py-1.5 text-sm text-ink-soft transition-colors hover:text-ink sm:inline";
export const pillCta = "rounded-full bg-ink px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-iris";
