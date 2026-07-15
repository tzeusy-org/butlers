/**
 * Shell-level sr-only announcer (bu-qvnce.10, JARVIS pursuit move 10).
 *
 * A single `aria-live="polite"` region, mounted once by RootLayout, that
 * screen-reader users hear no matter which page they're on. Before this,
 * "every degraded source names itself" (the fleet's honest-aggregation
 * doctrine) was visual-only: the shell's Live indicator swaps color/label
 * silently, page navigation never announces itself, and the ingestion
 * ledger's "N new events" pill is a purely visual affordance — none of it
 * reaches a screen-reader user.
 *
 * A plain module-level store (not React context) so any part of the app can
 * push an announcement from an effect, event handler, or hook without a
 * provider — the same "small event, no context" tradeoff already used by
 * `lib/entity-finder.ts`'s `OPEN_ENTITY_FINDER_EVENT` (a CustomEvent there;
 * a tiny external store here, since this needs to hold a current *value*
 * `useSyncExternalStore` can read, not just fire a signal).
 */

import { useSyncExternalStore } from "react";

let message = "";
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

/**
 * Push a new sr-only announcement to the shell's aria-live region.
 * No-ops on empty or duplicate-of-current text — a live region re-announcing
 * identical text is silent to screen readers anyway and would just be noise
 * in the DOM.
 */
export function announce(text: string): void {
  if (!text || text === message) return;
  message = text;
  emit();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): string {
  return message;
}

function getServerSnapshot(): string {
  return "";
}

/** Read the current shell announcement — used by the region component itself. */
export function useShellAnnouncement(): string {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
