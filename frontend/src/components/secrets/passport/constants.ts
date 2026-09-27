// ---------------------------------------------------------------------------
// Passport constants — state catalog and spine helpers [bu-qu8v8]
// ---------------------------------------------------------------------------

import type { CredentialState, SpineGroupId, StateMeta } from "./types.ts";

// ── State catalog ────────────────────────────────────────────────────────────
// rank: severity sort order. 0 = most urgent, 99 = quietest.

export const STATE_CATALOG: Record<CredentialState, StateMeta> = {
  expired:       { label: "expired",        tone: "red",   sliver: true,  rank: 0 },
  revoked:       { label: "revoked",        tone: "red",   sliver: true,  rank: 1 },
  scope_mismatch:{ label: "scope mismatch", tone: "amber", sliver: true,  rank: 2 },
  expiring:      { label: "expiring",       tone: "amber", sliver: true,  rank: 3 },
  // bu-976n0 (tri-state): "warn" means set-but-never-probed (or a prior probe
  // this stale, per _derive_state) — an UNKNOWN, not a BROKEN credential. It
  // used to render identically to a genuine failure (amber + sliver), which
  // fabricated alarm for the ~19-of-22 rows that were merely unverified on
  // 2026-07-05. Quiet/dim, no sliver — see NEEDS_HAND_STATES / UNVERIFIED_STATES.
  warn:          { label: "unverified",     tone: "dim",   sliver: false, rank: 4 },
  checking:      { label: "checking…",      tone: "dim",   sliver: false, rank: 5 },
  authorization_needed: {
    label: "authorization needed", tone: "amber", sliver: true, rank: 3,
  },
  rotating:      { label: "rotating…", tone: "dim",   sliver: false, rank: 4 },
  ok:            { label: "healthy",        tone: "ok",    sliver: false, rank: 5 },
  failed:        { label: "failed",         tone: "red",   sliver: true,  rank: 1 },
  never_set:     { label: "not set",        tone: "dim",   sliver: false, rank: 9 },
};

/** Fixed state-first group order for rendering, search, and roving focus. */
export const SPINE_GROUP_ORDER: readonly SpineGroupId[] = [
  "needs-hand",
  "in-progress",
  "stale",
  "ready",
  "not-set",
];

/** Exhaustive single-home mapping for every API credential state. */
export const SPINE_GROUP_BY_STATE: Record<CredentialState, SpineGroupId> = {
  expired: "needs-hand",
  revoked: "needs-hand",
  scope_mismatch: "needs-hand",
  expiring: "needs-hand",
  authorization_needed: "needs-hand",
  failed: "needs-hand",
  checking: "in-progress",
  rotating: "in-progress",
  warn: "stale",
  ok: "ready",
  never_set: "not-set",
};

function statesInGroup(group: SpineGroupId): Set<CredentialState> {
  return new Set(
    (Object.keys(SPINE_GROUP_BY_STATE) as CredentialState[]).filter(
      (state) => SPINE_GROUP_BY_STATE[state] === group,
    ),
  );
}

/** Genuinely broken or owner-actionable states. */
export const NEEDS_HAND_STATES = statesInGroup("needs-hand");

/** Quiet set-but-unverified states. */
const STALE_STATES = statesInGroup("stale");

/**
 * States that are merely unverified — set, but with no successful probe on
 * record (or a stale one). Quiet/gray, never alarm-colored: this bucket
 * should be near-empty and self-clearing once the background staleness loop
 * (bu-a63hn) re-probes it, not a standing amber alarm (bu-976n0).
 */
export const UNVERIFIED_STATES = STALE_STATES;

export function spineGroupForState(state: CredentialState): SpineGroupId {
  return SPINE_GROUP_BY_STATE[state];
}

export function needsHand(state: CredentialState): boolean {
  return NEEDS_HAND_STATES.has(state);
}

export function isUnverified(state: CredentialState): boolean {
  return UNVERIFIED_STATES.has(state);
}

export function severityRank(state: CredentialState): number {
  return STATE_CATALOG[state]?.rank ?? 99;
}

// ── Focus key helpers ────────────────────────────────────────────────────────

/** Parse a focus key: `u:google` → { family: 'u', id: 'google' } */
export function parseFocus(key: string): { family: "u" | "s" | "c"; id: string } | null {
  const idx = key.indexOf(":");
  if (idx === -1) return null;
  const family = key.slice(0, idx) as "u" | "s" | "c";
  if (!["u", "s", "c"].includes(family)) return null;
  const id = key.slice(idx + 1);
  if (!id) return null;
  return { family, id };
}

// ── Stamp glyphs ─────────────────────────────────────────────────────────────

export const STAMP_GLYPHS: Record<string, { glyph: string; tone: string }> = {
  verified:    { glyph: "✓", tone: "ok"     },
  rotated:     { glyph: "↻", tone: "fg"     },
  failed:      { glyph: "✕", tone: "red"    },
  revoked:     { glyph: "⊘", tone: "red"    },
  connected:   { glyph: "⊕", tone: "fg"     },
  disconnected:{ glyph: "⊖", tone: "dim"    },
  warned:      { glyph: "!", tone: "amber"  },
  overrode:    { glyph: "⤳", tone: "fg"     },
  attempted:   { glyph: "▷", tone: "dim"    },
  set:         { glyph: "⊙", tone: "fg"     },
};

// ── Severity metadata ────────────────────────────────────────────────────────
