// ---------------------------------------------------------------------------
// Passport constants — state catalog, tweaks keys, spine helpers [bu-qu8v8]
// ---------------------------------------------------------------------------

import type { CredentialState, StateMeta, SecretsTweaks } from "./types.ts";

// ── State catalog ────────────────────────────────────────────────────────────
// rank: severity sort order. 0 = most urgent, 99 = quietest.

export const STATE_CATALOG: Record<CredentialState, StateMeta> = {
  expired:       { label: "expired",        tone: "red",   sliver: true,  rank: 0 },
  revoked:       { label: "revoked",        tone: "red",   sliver: true,  rank: 1 },
  scope_mismatch:{ label: "scope mismatch", tone: "amber", sliver: true,  rank: 2 },
  expiring:      { label: "expiring",       tone: "amber", sliver: true,  rank: 3 },
  rotating:      { label: "rotating…", tone: "amber", sliver: false, rank: 4 },
  ok:            { label: "healthy",        tone: "ok",    sliver: false, rank: 5 },
  failed:        { label: "failed",         tone: "red",   sliver: true,  rank: 1 },
  never_set:     { label: "not set",        tone: "dim",   sliver: false, rank: 9 },
};

/** States that are "needs hand" — pinned at the top of the spine. */
export const NEEDS_HAND_STATES = new Set<CredentialState>([
  "expired", "revoked", "scope_mismatch", "expiring", "rotating", "failed",
]);

export function needsHand(state: CredentialState): boolean {
  return NEEDS_HAND_STATES.has(state);
}

export function severityRank(state: CredentialState): number {
  return STATE_CATALOG[state]?.rank ?? 99;
}

// ── localStorage keys ────────────────────────────────────────────────────────
// Keyed by `secrets.tweaks.*` per spec §Tweaks-Panel State Persistence.

export const TWEAKS_KEYS = {
  revealMode:    "secrets.tweaks.revealMode",
  defaultSort:   "secrets.tweaks.defaultSort",
  showVerifyCmd: "secrets.tweaks.showVerifyCmd",
  voiceParagraph:"secrets.tweaks.voiceParagraph",
} as const;

export const TWEAKS_DEFAULTS: SecretsTweaks = {
  revealMode:    "eye",
  defaultSort:   "severity",
  showVerifyCmd: false,
  voiceParagraph: true,
};

// ── Focus key helpers ────────────────────────────────────────────────────────

/** Encode a focus key from family + id. */
export function encodeFocus(family: "u" | "s" | "c", id: string): string {
  return `${family}:${id}`;
}

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

export const SEVERITY_META: Record<string, { glyph: string; label: string; tone: string }> = {
  high:   { glyph: "▰", label: "breaks",   tone: "red"   },
  medium: { glyph: "▰", label: "degrades", tone: "amber" },
  low:    { glyph: "▱", label: "minor",    tone: "dim"   },
};
