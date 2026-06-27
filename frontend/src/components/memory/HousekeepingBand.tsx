// ---------------------------------------------------------------------------
// HousekeepingBand — Band 4 of the /memory house-ledger (bu-2ix8d.8)
//
// The quiet back office: retention policies, the compaction log, and re-embed
// controls in ONE band at the foot of /memory under a single mono HOUSEKEEPING
// eyebrow. Visually subordinate to the registers above: 13px-and-below type,
// hairline dividers, no cards, no panel chrome, no section backgrounds. The
// attention rail's "stale embeddings" row deep-links to the `#housekeeping`
// anchor this band carries.
//
// Three hairline-divided sub-surfaces, each with AT MOST one commit-class
// action (Dispatch's one-commit-per-surface rule):
//   1. Retention policies — rule-grid; kind constrained to the backend's valid
//      set; a single dirty-state Save commit pill (the band's one Save).
//   2. Compaction log — read-only quiet list; bytes omitted when null.
//   3. Embeddings — inline dry-run result line; pill-morph re-embed confirm
//      (arm-then-commit, NO modal); mono status line; NO progress bar.
//
// Binding docs:
// - (memory house-ledger redesign, graduated) prompts/07-housekeeping.md
// - (memory house-ledger redesign, graduated) MEMORY_LANGUAGE.md §2, §4, §6, §8
// ---------------------------------------------------------------------------

import { useEffect, useMemo, useRef, useState } from "react";

import { Eyebrow } from "@/components/ui/Eyebrow";
import { Mono } from "@/components/ui/Mono";
import { Voice } from "@/components/ui/Voice";
import { useButlers } from "@/hooks/use-butlers";
import {
  useMemoryCompactionLog,
  useMemoryRetentionPolicies,
  useUpdateMemoryRetentionPolicies,
} from "@/hooks/use-memory";
import { useReembedPending, useReembedRun } from "@/hooks/use-memory-reembed";
import {
  dryRunResultLine,
  embeddingDriftSentence,
  formatCompactionCounts,
  formatUpdatedStamp,
  isValidRetentionKind,
  reembedDoneLine,
} from "@/lib/memory-housekeeping";
import { cn } from "@/lib/utils";
import type {
  MemoryRetentionPolicy,
  UpdateRetentionPolicyEntry,
} from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Shared sub-surface scaffolding
// ---------------------------------------------------------------------------

/** A borderless mono numeric input: hairline appears only on focus. */
function MonoCellInput({
  value,
  ariaLabel,
  onChange,
}: {
  value: number | null;
  ariaLabel: string;
  onChange: (v: number | null) => void;
}) {
  return (
    <input
      type="text"
      inputMode="numeric"
      aria-label={ariaLabel}
      value={value == null ? "" : String(value)}
      placeholder="—"
      onChange={(e) => {
        const raw = e.target.value.trim();
        if (raw === "") {
          onChange(null);
          return;
        }
        const n = Number.parseInt(raw, 10);
        if (Number.isNaN(n) || n < 0) return;
        onChange(n);
      }}
      className={cn(
        "w-20 bg-transparent font-mono text-[11px] tabular-nums leading-[1.4] text-[var(--fg)]",
        "border-0 border-b border-transparent px-0 py-0.5",
        "placeholder:text-[var(--mfg)]",
        "focus:border-[var(--border-strong)] focus:outline-none",
      )}
    />
  );
}

// ---------------------------------------------------------------------------
// 1 · Retention policies
// ---------------------------------------------------------------------------

type RetentionEdit = { ttl_days: number | null; max_rows: number | null };

/**
 * The retention rule-grid. Existing rows only (no kind creation), kind shown as
 * mono 11px; TTL and max-rows are borderless mono inputs. Edits track locally;
 * a single Save commit pill appears only when a row is dirty, PUTs all dirty
 * rows on click, then disappears. No toast — the refreshed `updated` stamp is
 * the confirmation.
 */
function RetentionPolicies() {
  const { data: policiesResp, isLoading } = useMemoryRetentionPolicies();
  const updateMutation = useUpdateMemoryRetentionPolicies();

  const policies = useMemo<MemoryRetentionPolicy[]>(
    () => policiesResp?.data ?? [],
    [policiesResp],
  );

  // Local edits keyed by kind. A kind is present here only after the user
  // touches it; the dirty set is the keys whose values differ from the row.
  const [edits, setEdits] = useState<Map<string, RetentionEdit>>(new Map());

  // Kinds skipped at save time because they fail the backend's kind guard.
  // Rows are server-sourced so this is defensive, but a malformed kind must
  // never reach the PUT — we surface the skip rather than silently drop it.
  const [skippedKinds, setSkippedKinds] = useState<string[]>([]);

  function baselineFor(kind: string): RetentionEdit {
    const row = policies.find((p) => p.kind === kind);
    return { ttl_days: row?.ttl_days ?? null, max_rows: row?.max_rows ?? null };
  }

  function handleChange(
    kind: string,
    field: keyof RetentionEdit,
    value: number | null,
  ) {
    setEdits((prev) => {
      const current = prev.get(kind) ?? baselineFor(kind);
      return new Map(prev).set(kind, { ...current, [field]: value });
    });
  }

  // A row is dirty when its tracked edit differs from its baseline.
  const dirtyKinds = useMemo(() => {
    const dirty: string[] = [];
    for (const [kind, edit] of edits) {
      const base = (() => {
        const row = policies.find((p) => p.kind === kind);
        return {
          ttl_days: row?.ttl_days ?? null,
          max_rows: row?.max_rows ?? null,
        };
      })();
      if (edit.ttl_days !== base.ttl_days || edit.max_rows !== base.max_rows) {
        dirty.push(kind);
      }
    }
    return dirty;
  }, [edits, policies]);

  const isDirty = dirtyKinds.length > 0;

  function valueFor(policy: MemoryRetentionPolicy, field: keyof RetentionEdit) {
    const edit = edits.get(policy.kind);
    if (edit) return edit[field];
    return policy[field];
  }

  function handleSave() {
    if (!isDirty || updateMutation.isPending) return;
    // Defensive guard: rows are server-sourced and the grid offers no kind
    // creation, but validate each dirty kind against the backend's accepted
    // set before send. Invalid kinds are skipped (surfaced below) rather than
    // PUT — a bad kind must never reach the API.
    const validKinds = dirtyKinds.filter(isValidRetentionKind);
    const skipped = dirtyKinds.filter((kind) => !isValidRetentionKind(kind));
    setSkippedKinds(skipped);
    if (validKinds.length === 0) return;
    const entries: UpdateRetentionPolicyEntry[] = validKinds.map((kind) => {
      const edit = edits.get(kind) ?? baselineFor(kind);
      return { kind, ttl_days: edit.ttl_days, max_rows: edit.max_rows };
    });
    updateMutation.mutate(
      { policies: entries },
      {
        // Clear local edits on success: the query invalidates and re-fetches
        // with the new `updated` stamps, and the Save pill disappears because
        // no row is dirty any longer. No toast — the stamp is the confirmation.
        onSuccess: () => {
          setEdits(new Map());
          setSkippedKinds([]);
        },
      },
    );
  }

  return (
    <section className="flex flex-col gap-3" aria-label="Retention policies">
      <div className="flex items-baseline justify-between">
        <Eyebrow as="div">Retention</Eyebrow>
        {/* The band's single Save commit pill — present only when dirty. */}
        {isDirty && (
          <button
            type="button"
            onClick={handleSave}
            disabled={updateMutation.isPending}
            className={cn(
              "inline-flex h-6 items-center rounded-full px-2.5",
              "font-mono text-[10px] font-medium uppercase tracking-wide leading-none",
              "border border-[var(--fg)] text-[var(--fg)] bg-transparent",
              "transition-colors hover:bg-[var(--fg)] hover:text-[var(--bg)]",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30",
              "disabled:pointer-events-none disabled:opacity-40",
            )}
          >
            {updateMutation.isPending ? "saving…" : "save"}
          </button>
        )}
      </div>

      {isLoading ? (
        <Mono muted>loading…</Mono>
      ) : policies.length === 0 ? (
        <Voice variant="italic">No retention policies set.</Voice>
      ) : (
        <div role="table" className="flex flex-col">
          {/* Column header — mono eyebrow scale, hairline below. */}
          <div
            role="row"
            className="grid grid-cols-[1fr_5rem_6rem_auto] items-baseline gap-x-6 border-b border-[var(--border-soft)] pb-2"
          >
            <Eyebrow as="div">kind</Eyebrow>
            <Eyebrow as="div">ttl days</Eyebrow>
            <Eyebrow as="div">max rows</Eyebrow>
            <Eyebrow as="div" className="text-right">
              updated
            </Eyebrow>
          </div>
          {policies.map((p) => (
            <div
              key={p.kind}
              role="row"
              className="grid grid-cols-[1fr_5rem_6rem_auto] items-baseline gap-x-6 border-b border-[var(--border-soft)] py-2 last:border-b-0"
            >
              <Mono>{p.kind}</Mono>
              <MonoCellInput
                value={valueFor(p, "ttl_days")}
                ariaLabel={`${p.kind} ttl days`}
                onChange={(v) => handleChange(p.kind, "ttl_days", v)}
              />
              <MonoCellInput
                value={valueFor(p, "max_rows")}
                ariaLabel={`${p.kind} max rows`}
                onChange={(v) => handleChange(p.kind, "max_rows", v)}
              />
              <Mono muted className="text-right">
                {formatUpdatedStamp(p.updated_at, p.updated_by)}
              </Mono>
            </div>
          ))}
        </div>
      )}

      {skippedKinds.length > 0 && (
        <Mono muted className="text-[var(--red)]">
          skipped invalid {skippedKinds.length === 1 ? "kind" : "kinds"}:{" "}
          {skippedKinds.join(", ")}
        </Mono>
      )}

      {updateMutation.isError && (
        <Mono muted className="text-[var(--red)]">
          save failed, try again
        </Mono>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// 2 · Compaction log
// ---------------------------------------------------------------------------

/**
 * Read-only quiet list of the last 50 compaction sweeps. Mono time · mono kind
 * · sans counts; bytes omitted (not em-dashed) when null. No pagination — the
 * 50 rows scroll within the page flow. Empty: serif-italic "No sweeps
 * recorded."
 */
function CompactionLog() {
  const { data: logResp, isLoading } = useMemoryCompactionLog(50);
  const entries = logResp?.data ?? [];

  return (
    <section className="flex flex-col gap-3" aria-label="Compaction log">
      <Eyebrow as="div">Compaction</Eyebrow>
      {isLoading ? (
        <Mono muted>loading…</Mono>
      ) : entries.length === 0 ? (
        <Voice variant="italic">No sweeps recorded.</Voice>
      ) : (
        <div className="flex flex-col">
          {entries.map((e) => {
            const d = new Date(e.ts);
            const time = Number.isNaN(d.getTime())
              ? "--:--"
              : `${String(d.getHours()).padStart(2, "0")}:${String(
                  d.getMinutes(),
                ).padStart(2, "0")}`;
            return (
              <div
                key={e.id}
                className="grid grid-cols-[3.5rem_6rem_1fr] items-baseline gap-x-4 py-1.5"
              >
                <Mono muted>{time}</Mono>
                <Mono>{e.kind}</Mono>
                <span className="text-[13px] leading-snug text-[var(--mfg)]">
                  {formatCompactionCounts(e.rows_removed, e.bytes_freed)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// 3 · Embeddings
// ---------------------------------------------------------------------------

const REEMBED_CONFIRM_WINDOW_MS = 5000;

/**
 * The re-embed surface, compressed to a single quiet line plus two pills.
 *
 * Drift: one sans sentence (summed across tiers); serif-italic "All embeddings
 * current." when zero.
 * `dry run` — secondary pill; renders its result inline as one mono line, no
 * modal.
 * `re-embed` — long synchronous run. The confirm step is an inline pill-morph
 * (`re-embed (confirm?)` for 5s), NOT a dialog. While running: a `composing…`
 * mono status line. On completion: one mono line `re-embedded N rows · Ns`.
 * NO progress bar.
 */
function Embeddings() {
  const { data: pendingResp, isLoading } = useReembedPending();
  const reembedMutation = useReembedRun();

  // The pending count probes the first available pool (alphabetically-first
  // butler — see _memory_pool_names in the backend). The POST /reembed needs a
  // concrete butler, so mirror that default client-side: the first butler name
  // sorted ascending. This keeps the re-embed action LIVE rather than a dead
  // disabled pill, and targets the same pool the counts were measured on.
  const { data: butlersResp } = useButlers();
  const defaultButler = useMemo(() => {
    const names = (butlersResp?.data ?? []).map((b) => b.name);
    return names.length > 0 ? [...names].sort()[0] : undefined;
  }, [butlersResp]);

  const pending = pendingResp?.data;
  const total = pending?.total ?? 0;
  const driftSentence = embeddingDriftSentence(total);

  // Arm-then-commit state for the re-embed pill-morph.
  const [armed, setArmed] = useState(false);
  const armTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Inline result lines (separate so a dry-run does not clobber a prior run).
  const [dryLine, setDryLine] = useState<string | null>(null);
  const [runLine, setRunLine] = useState<string | null>(null);
  const runStartedAt = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (armTimer.current) clearTimeout(armTimer.current);
    };
  }, []);

  function disarm() {
    if (armTimer.current) {
      clearTimeout(armTimer.current);
      armTimer.current = null;
    }
    setArmed(false);
  }

  const isPending = reembedMutation.isPending;
  const dryRunInFlight = isPending && reembedMutation.variables?.dry_run === true;
  const runInFlight = isPending && reembedMutation.variables?.dry_run === false;

  function handleDryRun() {
    if (isPending || !pending) return;
    disarm();
    setDryLine(null);
    reembedMutation.mutate(
      { butler: defaultButler ?? "", dry_run: true, current_model: pending.current_model },
      {
        onSuccess: (resp) => {
          setDryLine(
            dryRunResultLine(resp.data.total, resp.data.tiers_processed.length),
          );
        },
      },
    );
  }

  function handleReembedClick() {
    if (isPending || !pending || !defaultButler) return;
    if (!armed) {
      // First click arms; auto-disarms after the confirm window.
      setArmed(true);
      if (armTimer.current) clearTimeout(armTimer.current);
      armTimer.current = setTimeout(() => {
        armTimer.current = null;
        setArmed(false);
      }, REEMBED_CONFIRM_WINDOW_MS);
      return;
    }
    // Second click within the window commits.
    disarm();
    setRunLine(null);
    runStartedAt.current = Date.now();
    reembedMutation.mutate(
      { butler: defaultButler, dry_run: false, current_model: pending.current_model },
      {
        onSuccess: (resp) => {
          const elapsedMs = runStartedAt.current
            ? Date.now() - runStartedAt.current
            : 0;
          setRunLine(reembedDoneLine(resp.data.total, elapsedMs / 1000));
        },
      },
    );
  }

  const canReembed = !!defaultButler;

  return (
    <section className="flex flex-col gap-3" aria-label="Embeddings">
      <Eyebrow as="div">Embeddings</Eyebrow>

      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        {/* Drift sentence (or serif-italic "current" line). */}
        {isLoading ? (
          <Mono muted>loading…</Mono>
        ) : driftSentence == null ? (
          <Voice variant="italic">All embeddings current.</Voice>
        ) : (
          <span className="text-[13px] leading-snug text-[var(--fg)]">
            {driftSentence}
          </span>
        )}

        {/* Action pills: dry run (secondary) · re-embed (pill-morph confirm). */}
        <div className="flex items-center gap-4">
          <button
            type="button"
            onClick={handleDryRun}
            disabled={isPending || !pending || total === 0}
            className={cn(
              "font-mono text-[11px] leading-[1.4] text-[var(--mfg)]",
              "underline [text-underline-offset:4px]",
              "transition-colors hover:text-[var(--fg)]",
              "disabled:pointer-events-none disabled:opacity-40",
            )}
          >
            {dryRunInFlight ? "running…" : "dry run"}
          </button>
          <button
            type="button"
            onClick={handleReembedClick}
            onBlur={disarm}
            disabled={isPending || !pending || total === 0 || !canReembed}
            className={cn(
              "font-mono text-[11px] leading-[1.4]",
              "underline [text-underline-offset:4px]",
              "transition-colors",
              armed
                ? "text-[var(--fg)]"
                : "text-[var(--mfg)] hover:text-[var(--fg)]",
              "disabled:pointer-events-none disabled:opacity-40",
            )}
          >
            {runInFlight
              ? "composing…"
              : armed
                ? "re-embed (confirm?)"
                : "re-embed"}
          </button>
        </div>
      </div>

      {/* Inline dry-run result line — one mono line, no modal. */}
      {dryLine && <Mono muted>{dryLine}</Mono>}

      {/* Running status line (NO progress bar) and completion line. */}
      {runInFlight && <Mono muted>composing…</Mono>}
      {runLine && !runInFlight && <Mono muted>{runLine}</Mono>}

      {reembedMutation.isError && (
        <Mono muted className="text-[var(--red)]">
          re-embed failed, try again
        </Mono>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// HousekeepingBand
// ---------------------------------------------------------------------------

/**
 * Band 4. One HOUSEKEEPING eyebrow over three hairline-divided sub-surfaces,
 * carrying the `#housekeeping` anchor the attention rail deep-links to. Quiet
 * by construction: no cards, no panel chrome, small type throughout.
 */
export default function HousekeepingBand() {
  return (
    <section
      id="housekeeping"
      aria-label="Housekeeping"
      className="flex scroll-mt-6 flex-col gap-8 border-t border-[var(--border-soft)] pt-8"
    >
      <Eyebrow as="div">Housekeeping</Eyebrow>

      <div className="flex flex-col divide-y divide-[var(--border-soft)]">
        <div className="pb-8">
          <RetentionPolicies />
        </div>
        <div className="py-8">
          <CompactionLog />
        </div>
        <div className="pt-8">
          <Embeddings />
        </div>
      </div>
    </section>
  );
}
