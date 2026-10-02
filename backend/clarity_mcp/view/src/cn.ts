/** Same role as the web app's cn (frontend/src/lib/utils.ts): join the truthy class names. */
export const cn = (...c: unknown[]) => c.filter(Boolean).join(" ");
