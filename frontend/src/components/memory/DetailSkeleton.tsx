// ---------------------------------------------------------------------------
// DetailSkeleton — the shared editorial page-shape for the three memory detail
// pages (fact / rule / episode). (bu-2ix8d.7)
//
// All three pages render the same skeleton, in order (prompts/06-detail-pages.md):
//
//   1. Eyebrow      — kind + short id (`FACT · 7A3F21C9`), mono 10px.
//   2. Heading      — the content itself (sans 24px/500). The memory IS the
//                     headline; there is no "Fact Details" title chrome.
//   3. State line   — one mono 11px line in the API's words
//                     (`active · standard permanence · scope health`). Dimmed
//                     throughout when the record is fading.
//   4. KV band      — dense two-column hairline grid (mono keys, sans values).
//                     Empty keys are omitted entirely.
//   5. Kind section — supplied by the caller (decay line / outcome record /
//                     episode body).
//   6. Provenance   — PROVENANCE eyebrow + chain list, OMITTED when empty (no
//                     empty shell). Supplied by the caller.
//   7. Commit footer — facts only; supplied by the caller, gated on endpoints.
//
// Color discipline (MEMORY_LANGUAGE.md §6): a healthy detail page renders zero
// red/amber/green pixels. The only state color permitted here is the rule's
// `--red` harmful tally (> 0), rendered by the caller's kind section.
//
// Binding docs:
// - (memory house-ledger redesign, graduated) prompts/06-detail-pages.md
// - (memory house-ledger redesign, graduated) MEMORY_LANGUAGE.md §4, §6
// ---------------------------------------------------------------------------

import { type ReactNode } from "react";
import { Link } from "react-router";

import { Eyebrow } from "@/components/ui/Eyebrow";
import { Mono } from "@/components/ui/Mono";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Short id
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Eyebrow — kind + short id
// ---------------------------------------------------------------------------

/**
 * `FACT · 7A3F21C9` — uppercase kind, mono short id (first 8 chars, hyphens
 * stripped, uppercased).
 */
export function DetailEyebrow({ kind, id }: { kind: string; id: string }) {
  const short = id.replace(/-/g, "").slice(0, 8).toUpperCase();
  return (
    <Eyebrow as="div">
      {kind.toUpperCase()} · {short}
    </Eyebrow>
  );
}

// ---------------------------------------------------------------------------
// Heading — the content as the headline
// ---------------------------------------------------------------------------

/**
 * The memory content rendered as the page heading (sans 24px/500). This is the
 * single H1 on the page — no "Details" chrome. Dimmed to `--dim` when `dimmed`.
 *
 * An optional `subtitle` renders as a plain mono line directly below the H1 —
 * the record-identity line the detail-page archetype mandates (subject ·
 * predicate for a fact, the session reference for an episode). It is omitted
 * entirely when empty, and dims alongside the heading on a fading record.
 */
export function DetailHeading({
  children,
  subtitle,
  dimmed = false,
}: {
  children: ReactNode;
  subtitle?: ReactNode;
  dimmed?: boolean;
}) {
  const hasSubtitle = subtitle != null && subtitle !== "";
  return (
    <div className="flex flex-col gap-1.5">
      <h1
        className={cn(
          "text-[24px] font-medium leading-snug tracking-tight",
          "whitespace-pre-wrap break-words",
          dimmed ? "text-[var(--dim)]" : "text-[var(--fg)]",
        )}
      >
        {children}
      </h1>
      {hasSubtitle ? (
        <p
          className={cn(
            "font-mono text-[12px] leading-snug",
            dimmed ? "text-[var(--dim)]" : "text-[var(--mfg)]",
          )}
        >
          {subtitle}
        </p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// State line — lifecycle in the API's words
// ---------------------------------------------------------------------------

/**
 * One mono line stating lifecycle state in the API's words, the fragments
 * joined by ` · `. Empty/nullish fragments are dropped. Dimmed throughout when
 * `dimmed` (a fading record reads its whole state line at `--dim`).
 */
export function StateLine({
  fragments,
  dimmed = false,
}: {
  fragments: (string | null | undefined)[];
  dimmed?: boolean;
}) {
  const parts = fragments.filter((f): f is string => !!f && f.length > 0);
  if (parts.length === 0) return null;
  return (
    <Mono muted={dimmed} className={cn(dimmed && "text-[var(--dim)]")}>
      {parts.join(" · ")}
    </Mono>
  );
}

// ---------------------------------------------------------------------------
// KV band — dense two-column hairline grid
// ---------------------------------------------------------------------------

/** One key/value pair for the KV band. Rows with a nullish value are omitted. */
export interface KVEntry {
  key: string;
  /** Omit the row entirely when this is null/undefined/"". */
  value: ReactNode | null | undefined;
}

/** True when a KV value is renderable (not null/undefined/empty string). */
function hasValue(value: ReactNode | null | undefined): boolean {
  return value != null && value !== "";
}

/**
 * Dense two-column hairline grid: mono keys, sans values. Empty entries are
 * omitted entirely (the band never shows a key with a blank value). Renders
 * nothing when no entries survive.
 */
export function KVBand({ entries }: { entries: KVEntry[] }) {
  const rows = entries.filter((e) => hasValue(e.value));
  if (rows.length === 0) return null;
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-6">
      {rows.map((row) => (
        <div
          key={row.key}
          className="contents [&>dt]:border-b [&>dt]:border-[var(--border-soft)] [&>dd]:border-b [&>dd]:border-[var(--border-soft)]"
        >
          <dt className="py-2 font-mono text-[11px] text-[var(--mfg)] tabular-nums">
            {row.key}
          </dt>
          <dd className="py-2 text-[13px] leading-snug text-[var(--fg)]">
            {row.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

// ---------------------------------------------------------------------------
// Provenance section — omitted when empty
// ---------------------------------------------------------------------------

/**
 * `PROVENANCE` eyebrow + a chain list. The section is OMITTED entirely (returns
 * null) when no children are supplied — there is no empty shell. The caller
 * decides what counts as "empty" (e.g. no source episode AND no derived facts).
 */
export function ProvenanceSection({ children }: { children: ReactNode | null }) {
  if (children == null) return null;
  return (
    <section className="flex flex-col gap-2">
      <Eyebrow as="div">PROVENANCE</Eyebrow>
      <div className="flex flex-col gap-1.5">{children}</div>
    </section>
  );
}

/**
 * A single provenance chain link: `↳ <label>` linking to `to`. Mono, muted,
 * underlined on hover — the calm cross-reference affordance.
 */
export function ProvenanceLink({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className="inline-flex w-fit items-baseline gap-1.5 font-mono text-[11px] text-[var(--mfg)] hover:text-[var(--fg)]"
    >
      <span aria-hidden>↳</span>
      <span className="underline [text-underline-offset:3px]">{label}</span>
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Metadata block — the record's raw metadata bag as a mono code block
// ---------------------------------------------------------------------------

/**
 * `METADATA` eyebrow + the record's `metadata` bag rendered as a mono code
 * block (pretty-printed JSON). The detail-page archetype mandates this block
 * when metadata is non-empty; an empty/absent bag OMITS the section entirely
 * (no empty shell — same discipline as the KV band and provenance section).
 */
export function MetadataBlock({
  metadata,
}: {
  metadata: Record<string, unknown> | null | undefined;
}) {
  if (metadata == null || Object.keys(metadata).length === 0) return null;
  return (
    <section className="flex flex-col gap-2">
      <Eyebrow as="div">METADATA</Eyebrow>
      <pre className="overflow-x-auto rounded-md border border-[var(--border-soft)] p-3 font-mono text-[11px] leading-relaxed text-[var(--mfg)] whitespace-pre-wrap break-words">
        {JSON.stringify(metadata, null, 2)}
      </pre>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Page shell
// ---------------------------------------------------------------------------

/**
 * The editorial article shell shared by all three detail pages: a constrained
 * measure, a quiet back link to the register, then the skeleton sections in
 * order. Renders a serif-italic loading/empty line rather than a skeleton pulse
 * (MEMORY_LANGUAGE.md §8: no skeleton pulse).
 */
export function DetailSkeleton({
  backHref,
  backLabel,
  children,
}: {
  backHref: string;
  backLabel: string;
  children: ReactNode;
}) {
  return (
    <article className="mx-auto flex max-w-[680px] flex-col gap-6">
      <Link
        to={backHref}
        className="w-fit font-mono text-[11px] text-[var(--mfg)] hover:text-[var(--fg)]"
      >
        ← {backLabel}
      </Link>
      {children}
    </article>
  );
}
