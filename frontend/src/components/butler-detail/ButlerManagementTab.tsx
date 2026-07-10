/**
 * ButlerManagementTab — Phase 7 fold-in (§9.1–§9.4).
 *
 * Renders the six sections defined in the ButlersExpanded design:
 *   §1  Identity & routing   — fallback chain, schedule, ceiling, approvals, timeout, concurrency
 *   §2  System prompt        — serif body + version caption + edit modal
 *   §3  Tools matrix         — tool · description · scope · on toggles
 *   §4  Memory access        — short / mid / long read/write tiles
 *   §5  Activity stripe      — 24h sessions per hour
 *   §6  Kill switch          — 30s grace confirmation
 */

import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { useButlerMemoryAccess, useButlerPrompt, useButlerPromptHistory, useButlerTools, useKillButler, useUpdateButlerPrompt } from "@/hooks/use-butler-management";
import { useButlerHourlyActivity } from "@/hooks/use-butler-analytics";
import { useResolveModel } from "@/hooks/use-model-catalog";
import { useModalChoreography } from "@/hooks/use-modal-choreography";
import { bucketHourInZone } from "@/lib/hourly-buckets";
import { cn } from "@/lib/utils";
import { useTimezone } from "@/components/ui/timezone-context";
import RuntimeConfigCard from "./RuntimeConfigCard";

interface Props {
  butlerName: string;
}

/**
 * Complexity tier used for the read-only "resolved model" preview in §1
 * Identity & routing. Must be one of the backend's valid tiers
 * (reasoning/workhorse/cheap/specialty/local/legacy -- see
 * model_settings.py:_COMPLEXITY_TIERS); "workhorse" mirrors the backend
 * TriggerRequest default (matches ButlerDetailActions.tsx's
 * DEFAULT_COMPLEXITY, bu-86c4c.18).
 */
const DEFAULT_COMPLEXITY = "workhorse";

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

/**
 * ModalBackdrop — click-outside-to-close overlay with the one overlay
 * contract (bu-qvnce.10: focus-in, Tab trap, Escape, focus-restore) via
 * useModalChoreography. Previously: Escape-to-close only, no focus-in, no
 * trap, no restore.
 *
 * Clicking the backdrop itself (never a descendant, via the target check
 * below) calls `onClose`. Children never need their own stopPropagation
 * click handler, so only this one element carries the mouse-only
 * backdrop-dismiss gesture — Escape is its real keyboard equivalent
 * (bu-86c4c.16: a div that only ever receives synthetic-target clicks has no
 * sensible focus target of its own for Enter/Space).
 */
function ModalBackdrop({
  onClose,
  label,
  children,
}: {
  onClose: () => void;
  /** Accessible name for the dialog root (e.g. "Edit system prompt · general"). */
  label: string;
  children: React.ReactNode;
}) {
  // focusRoot: true — the root itself is both the Tab-trap boundary and the
  // initial-focus target (tabIndex={-1} below), since ModalBackdrop doesn't
  // own any particular element inside `children` to focus instead (callers
  // supply arbitrary panel content).
  const { rootRef, onKeyDown } = useModalChoreography<HTMLDivElement>({ onClose, focusRoot: true });

  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- role="dialog" + tabIndex={-1} + onClick/onKeyDown is the standard WAI-ARIA APG modal dialog pattern (backdrop-dismiss on click, Escape + Tab-trap via useModalChoreography's onKeyDown); jsx-a11y's "non-interactive roles" list includes "dialog", which is a false positive here, not a real a11y gap.
    <div
      ref={rootRef}
      role="dialog"
      aria-modal="true"
      aria-label={label}
      tabIndex={-1}
      // eslint-disable-next-line no-restricted-syntax -- wired through useModalChoreography above (rootRef/initialFocusRef/onKeyDown) — one overlay contract, not a hand-rolled one (bu-qvnce.10).
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 focus:outline-none"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={onKeyDown}
    >
      {children}
    </div>
  );
}

function SectionHeader({
  n,
  title,
  hint,
  right,
}: {
  n: number;
  title: string;
  hint?: string;
  right?: React.ReactNode;
}) {
  const nn = String(n).padStart(2, "0");
  return (
    <div className="mb-4 grid grid-cols-[2rem_1fr_auto] items-baseline gap-3">
      <span className="font-mono text-[10px] uppercase tracking-[0.06em] text-muted-foreground">
        §{nn}
      </span>
      <div>
        <h2 className="text-base font-medium leading-snug tracking-tight">{title}</h2>
        {hint && (
          <p className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.04em] text-muted-foreground">
            {hint}
          </p>
        )}
      </div>
      {right}
    </div>
  );
}

function Section({
  n,
  title,
  hint,
  right,
  children,
}: {
  n: number;
  title: string;
  hint?: string;
  right?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="border-b border-border px-7 py-6 last:border-b-0">
      <SectionHeader n={n} title={title} hint={hint} right={right} />
      {children}
    </section>
  );
}

function MonoCaption({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "font-mono text-[10px] uppercase tracking-[0.10em] text-muted-foreground",
        className,
      )}
    >
      {children}
    </span>
  );
}

function ConfigRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between border-b border-border/50 py-2 last:border-b-0">
      <MonoCaption>{label}</MonoCaption>
      <span className="font-mono text-xs text-foreground">{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// §1 Identity & routing (static from runtime config)
// ---------------------------------------------------------------------------

function IdentitySection({ butlerName }: { butlerName: string }) {
  // Model + per-session timeout are owned by the model catalog (resolved per
  // complexity tier), not runtime_config — core_073 moved session_timeout_s onto
  // public.model_catalog. We surface the resolved DEFAULT_COMPLEXITY-tier model
  // read-only here and defer all model/timeout editing to the Models tab.
  const { data: resolved, isLoading } = useResolveModel(butlerName, DEFAULT_COMPLEXITY);
  const r = resolved?.data;

  return (
    <Section n={1} title="Identity & routing" hint="model, fallback chain, schedule, ceilings">
      <div className="grid grid-cols-2 gap-8">
        <div>
          <div className="mb-2 flex items-baseline justify-between gap-3">
            <MonoCaption>model · {DEFAULT_COMPLEXITY} tier</MonoCaption>
            <Link
              to={`/butlers/${butlerName}?tab=models`}
              className="font-mono text-[10px] text-muted-foreground underline underline-offset-4 hover:text-foreground"
            >
              edit in models →
            </Link>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded border border-border bg-muted/30 px-2 py-1 font-mono text-[11px] text-foreground">
              <span className="text-[var(--green)]">resolved · </span>
              {isLoading ? "…" : r?.resolved && r.model_id ? r.model_id : "not configured"}
            </span>
          </div>
          <div className="mt-3 flex flex-col gap-1.5">
            <ConfigRow
              label="Session timeout"
              value={
                isLoading
                  ? "…"
                  : r?.resolved && r.session_timeout_s != null
                    ? `${r.session_timeout_s}s`
                    : "—"
              }
            />
          </div>
          <p className="mt-3 font-serif text-xs italic leading-relaxed text-muted-foreground">
            On primary failure the runtime tries each fallback in order with a 2s timeout. After
            three exhausted attempts the butler pauses and an approval is opened. Model selection and
            the per-session timeout are managed in the Models tab.
          </p>
        </div>
        {/*
          Editable runtime-config (concurrency / queue / core groups). Replaces the
          former read-only ConfigRows so the Manage tab is the single editable surface
          for these fields. RuntimeConfigCard surfaces hot vs cold (restart-required)
          tiers and the post-save "restart required for ..." notice.
        */}
        <RuntimeConfigCard butlerName={butlerName} />
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// §2 System prompt
// ---------------------------------------------------------------------------

function SystemPromptSection({ butlerName }: { butlerName: string }) {
  const { data, isLoading } = useButlerPrompt(butlerName);
  const [showEdit, setShowEdit] = useState(false);
  const [showDiff, setShowDiff] = useState(false);

  const pv = data?.data;
  const version = pv?.version ?? 0;
  const prompt = pv?.prompt ?? "";
  const updatedBy = pv?.updated_by ?? "—";

  return (
    <Section
      n={2}
      title="System prompt"
      hint={version > 0 ? `version ${version}` : "no prompt set"}
      right={
        version > 0 ? (
          <div className="flex gap-3">
            <Link
              to={`/butlers/${butlerName}?tab=config`}
              className="font-mono text-[11px] text-muted-foreground underline underline-offset-4 hover:text-foreground"
            >
              history · {version} version{version !== 1 ? "s" : ""} →
            </Link>
            {version > 1 && (
              <button
                type="button"
                className="font-mono text-[11px] text-muted-foreground underline underline-offset-4 hover:text-foreground"
                onClick={() => setShowDiff(true)}
              >
                diff vs v{version - 1} →
              </button>
            )}
          </div>
        ) : null
      }
    >
      {isLoading ? (
        <div className="h-20 w-full rounded bg-muted" />
      ) : (
        <>
          <div className="max-w-[72ch] rounded border border-border bg-muted/20 px-4 py-3 font-serif text-sm leading-relaxed text-foreground">
            {prompt || <span className="italic text-muted-foreground">No system prompt configured.</span>}
          </div>
          <div className="mt-2.5 flex items-center gap-3 font-mono text-[10px] text-muted-foreground">
            {prompt && (
              <>
                <span>tokens · {Math.round(prompt.length / 4)}</span>
                <span>·</span>
                <span>last edit · {updatedBy}</span>
              </>
            )}
            <span className="flex-1" />
            <button
              type="button"
              className="underline underline-offset-4 hover:text-foreground"
              onClick={() => setShowEdit(true)}
            >
              edit prompt →
            </button>
          </div>
        </>
      )}

      {/* Edit modal (lightweight inline) */}
      {showEdit && (
        <PromptEditModal butlerName={butlerName} onClose={() => setShowEdit(false)} currentPrompt={prompt} />
      )}

      {/* Diff modal — current head vs previous version */}
      {showDiff && (
        <PromptDiffModal
          butlerName={butlerName}
          currentVersion={version}
          onClose={() => setShowDiff(false)}
        />
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Prompt diff modal — line-level diff of the current head vs the prior version.
// Pulls both versions from the prompt-history reader (newest-first).
// ---------------------------------------------------------------------------

type DiffLine = { type: "added" | "removed" | "same"; text: string };

/** Minimal LCS-based line diff. Sufficient for short system prompts. */
function diffLines(a: string, b: string): DiffLine[] {
  const aLines = a.split("\n");
  const bLines = b.split("\n");
  const n = aLines.length;
  const m = bLines.length;
  // LCS length table.
  const lcs: number[][] = Array.from({ length: n + 1 }, () => Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] =
        aLines[i] === bLines[j]
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (aLines[i] === bLines[j]) {
      out.push({ type: "same", text: aLines[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ type: "removed", text: aLines[i] });
      i++;
    } else {
      out.push({ type: "added", text: bLines[j] });
      j++;
    }
  }
  while (i < n) out.push({ type: "removed", text: aLines[i++] });
  while (j < m) out.push({ type: "added", text: bLines[j++] });
  return out;
}

function PromptDiffModal({
  butlerName,
  currentVersion,
  onClose,
}: {
  butlerName: string;
  currentVersion: number;
  onClose: () => void;
}) {
  // Fetch enough history to include the current head and the prior version.
  const { data, isLoading } = useButlerPromptHistory(butlerName, { limit: 50 });
  const versions = data?.data ?? [];

  const current = versions.find((v) => v.version === currentVersion);
  const previous = versions.find((v) => v.version === currentVersion - 1);

  const lines =
    current && previous ? diffLines(previous.prompt, current.prompt) : [];

  return (
    <ModalBackdrop onClose={onClose} label={`Prompt diff · v${currentVersion - 1} to v${currentVersion} · ${butlerName}`}>
      <div className="flex max-h-[80vh] w-full max-w-2xl flex-col rounded-lg border border-border bg-background p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <span className="font-mono text-[11px] uppercase tracking-[0.10em] text-muted-foreground">
            diff · v{currentVersion - 1} → v{currentVersion} · {butlerName}
          </span>
          <button
            type="button"
            className="font-mono text-[11px] text-muted-foreground hover:text-foreground"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
        {isLoading ? (
          <div className="h-40 w-full rounded bg-muted" />
        ) : !current || !previous ? (
          <p className="font-mono text-[11px] text-muted-foreground">
            Could not load both versions to diff.
          </p>
        ) : (
          <div className="overflow-auto rounded border border-border bg-muted/10 p-3 font-mono text-[11px] leading-relaxed">
            {lines.map((line, idx) => (
              <div
                key={idx}
                className={cn(
                  "whitespace-pre-wrap",
                  line.type === "added" &&
                    "bg-[var(--green)]/10 text-[var(--green)]",
                  line.type === "removed" &&
                    "bg-[var(--red)]/10 text-[var(--red-text)]",
                  line.type === "same" && "text-muted-foreground",
                )}
              >
                <span className="select-none opacity-60">
                  {line.type === "added" ? "+ " : line.type === "removed" ? "- " : "  "}
                </span>
                {line.text || " "}
              </div>
            ))}
          </div>
        )}
      </div>
    </ModalBackdrop>
  );
}

function PromptEditModal({
  butlerName,
  onClose,
  currentPrompt,
}: {
  butlerName: string;
  onClose: () => void;
  currentPrompt: string;
}) {
  const [draft, setDraft] = useState(currentPrompt);
  const { mutate: updatePrompt, isPending } = useUpdateButlerPrompt(butlerName);

  function handleSave() {
    updatePrompt(
      { prompt: draft },
      {
        onSuccess: () => {
          toast.success("System prompt updated");
          onClose();
        },
        onError: (err) => {
          const msg = err instanceof Error ? err.message : "Failed to save system prompt";
          toast.error(msg);
        },
      },
    );
  }

  return (
    <ModalBackdrop onClose={onClose} label={`Edit system prompt · ${butlerName}`}>
      <div className="w-full max-w-2xl rounded-lg border border-border bg-background p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <span className="font-mono text-[11px] uppercase tracking-[0.10em] text-muted-foreground">
            edit system prompt · {butlerName}
          </span>
          <button
            type="button"
            className="font-mono text-[11px] text-muted-foreground hover:text-foreground"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
        <textarea
          className="h-64 w-full resize-none rounded border border-border bg-muted/20 p-3 font-serif text-sm leading-relaxed focus:outline-none focus:ring-1 focus:ring-ring"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Enter system prompt…"
        />
        <div className="mt-4 flex justify-end gap-3">
          <button
            type="button"
            className="font-mono text-[11px] text-muted-foreground hover:text-foreground"
            onClick={onClose}
          >
            cancel
          </button>
          <button
            type="button"
            disabled={isPending || draft === currentPrompt}
            className="rounded border border-border px-3 py-1.5 font-mono text-[11px] text-foreground hover:bg-muted disabled:opacity-50"
            onClick={handleSave}
          >
            {isPending ? "saving…" : "save version →"}
          </button>
        </div>
      </div>
    </ModalBackdrop>
  );
}

// ---------------------------------------------------------------------------
// §3 Tools matrix
// ---------------------------------------------------------------------------

function ToolsSection({ butlerName }: { butlerName: string }) {
  const { data, isLoading } = useButlerTools(butlerName);
  const tools = data?.data ?? [];

  return (
    <Section
      n={3}
      title="Tools & integrations"
      hint={
        tools.length > 0
          ? `${tools.filter((t) => t.allowed).length}/${tools.length} allowed`
          : "no tools configured"
      }
    >
      {isLoading ? (
        <div className="h-24 w-full rounded bg-muted" />
      ) : tools.length === 0 ? (
        <p className="font-mono text-[11px] text-muted-foreground">
          No tool grants configured for this butler.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-[10rem_1fr_1fr_2.5rem] gap-3 border-b border-border pb-2">
            <MonoCaption>tool</MonoCaption>
            <MonoCaption>description</MonoCaption>
            <MonoCaption>scope</MonoCaption>
            <MonoCaption>on</MonoCaption>
          </div>
          {tools.map((t) => (
            <div
              key={t.name}
              className="grid grid-cols-[10rem_1fr_1fr_2.5rem] items-center gap-3 border-b border-border/50 py-2.5 last:border-b-0"
            >
              <span className="font-mono text-[11px] text-foreground">{t.name}</span>
              <span className="text-xs text-muted-foreground">{t.description ?? "—"}</span>
              <span className="font-mono text-[10px] text-muted-foreground">{t.scope ?? "—"}</span>
              <span className="flex justify-end">
                <span
                  className={cn(
                    "h-4 w-4 rounded-full border",
                    t.allowed
                      ? "border-[var(--green)] bg-[var(--green)]/30"
                      : "border-border bg-transparent",
                  )}
                />
              </span>
            </div>
          ))}
        </>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// §4 Memory access
// ---------------------------------------------------------------------------

function MemoryAccessSection({ butlerName }: { butlerName: string }) {
  const { data, isLoading } = useButlerMemoryAccess(butlerName);
  const ma = data?.data;

  const tiers = ["short", "mid", "long"] as const;

  return (
    <Section n={4} title="Memory access" hint="which tiers this butler may read, write, and owns">
      {isLoading ? (
        <div className="h-20 w-full rounded bg-muted" />
      ) : (
        <div className="grid grid-cols-2 gap-8">
          <div className="grid grid-cols-3 divide-x divide-border rounded border border-border">
            {tiers.map((t) => {
              const r = ma?.read.includes(t) ?? false;
              const w = ma?.write.includes(t) ?? false;
              return (
                <div key={t} className="px-4 py-3">
                  <MonoCaption className="block mb-2">{t}-term</MonoCaption>
                  <div className="flex gap-1.5">
                    <span
                      className={cn(
                        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[10px] tracking-[0.04em]",
                        r
                          ? "border-[var(--green)] text-[var(--green)]"
                          : "border-border text-muted-foreground",
                      )}
                    >
                      {r ? "●" : "○"} read
                    </span>
                    <span
                      className={cn(
                        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[10px] tracking-[0.04em]",
                        w
                          ? "border-[var(--green)] text-[var(--green)]"
                          : "border-border text-muted-foreground",
                      )}
                    >
                      {w ? "●" : "○"} write
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
          <div>
            {ma?.namespace && <ConfigRow label="Namespace · owned" value={ma.namespace} />}
            {ma?.embedding_model && (
              <ConfigRow label="Embed model" value={ma.embedding_model} />
            )}
            {ma != null && (
              <ConfigRow
                label="Drops · 7d"
                value={
                  <span className={ma.drops_7d > 0 ? "text-[var(--amber-text)]" : "text-muted-foreground"}>
                    {ma.drops_7d}
                  </span>
                }
              />
            )}
          </div>
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// §5 Activity stripe (24h)
// ---------------------------------------------------------------------------

function ActivityStripeSection({ butlerName }: { butlerName: string }) {
  const { data } = useButlerHourlyActivity(butlerName);
  const buckets = data?.data?.buckets ?? [];
  const ownerTz = useTimezone();

  // Build a 24-slot array indexed by hour-of-day. Each backend `hour_start` is
  // a UTC-anchored instant; it must be slotted in the OWNER timezone so the
  // viewer's host zone never shifts the histogram (bu-8ogli). Slot i is thus
  // the owner-tz hour i, matching the `${i}:00` tooltip below.
  const values: number[] = Array(24).fill(0);
  for (const bucket of buckets) {
    const hourIndex = bucketHourInZone(bucket.hour_start, ownerTz);
    if (hourIndex === null) continue;
    values[hourIndex] = bucket.sessions_count;
  }

  const max = Math.max(...values, 1);

  return (
    <Section
      n={5}
      title="Activity · last 24 hours"
      hint="hour-buckets"
      right={
        <Link
          to={`/butlers/${butlerName}?tab=activity`}
          className="font-mono text-[11px] text-muted-foreground underline underline-offset-4 hover:text-foreground"
        >
          open audit log →
        </Link>
      }
    >
      {/* Stripe chart */}
      <div className="flex h-6 gap-px">
        {values.map((v, i) => (
          <div
            key={i}
            className={cn(
              "flex-1 rounded-[1px]",
              v === 0 ? "bg-muted" : "bg-foreground/60",
            )}
            style={{ opacity: v === 0 ? 0.4 : 0.3 + (v / max) * 0.7 }}
            title={`${String(i).padStart(2, "0")}:00 · ${v} session${v !== 1 ? "s" : ""}`}
          />
        ))}
      </div>
      <div className="mt-1.5 flex justify-between font-mono text-[9px] tracking-[0.10em] text-muted-foreground">
        <span>00</span>
        <span>06</span>
        <span>12</span>
        <span>18</span>
        <span>now</span>
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// §6 Kill switch
// ---------------------------------------------------------------------------

function KillSwitchSection({ butlerName }: { butlerName: string }) {
  const [showConfirm, setShowConfirm] = useState(false);
  const { mutate: kill, isPending } = useKillButler(butlerName);

  function handleConfirm() {
    kill(
      { grace_seconds: 30, actor: "owner" },
      { onSettled: () => setShowConfirm(false) },
    );
  }

  return (
    <Section n={6} title="Kill switch" hint="graceful shutdown with configurable grace window">
      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={() => setShowConfirm(true)}
          className="font-mono text-[11px] text-[var(--red-text)] underline underline-offset-4 hover:opacity-80"
        >
          kill switch · 30s grace →
        </button>
        <MonoCaption>
          sends shutdown signal; butler processes current session before exiting
        </MonoCaption>
      </div>

      {showConfirm && (
        <ModalBackdrop onClose={() => setShowConfirm(false)} label={`Confirm kill switch · ${butlerName}`}>
          <div className="w-full max-w-sm rounded-lg border border-border bg-background p-6 shadow-xl">
            <p className="mb-2 font-mono text-[11px] uppercase tracking-[0.10em] text-muted-foreground">
              confirm kill
            </p>
            <p className="mb-6 font-serif text-sm leading-relaxed text-foreground">
              Shutdown <strong>{butlerName}</strong> with 30s grace? The butler will finish its
              current session before exiting.
            </p>
            <div className="flex justify-end gap-3">
              <button
                type="button"
                className="font-mono text-[11px] text-muted-foreground hover:text-foreground"
                onClick={() => setShowConfirm(false)}
              >
                cancel
              </button>
              <button
                type="button"
                disabled={isPending}
                className="rounded border border-[var(--red)]/50 px-3 py-1.5 font-mono text-[11px] text-[var(--red-text)] hover:bg-[var(--red)]/10 disabled:opacity-50"
                onClick={handleConfirm}
              >
                {isPending ? "shutting down…" : "confirm shutdown →"}
              </button>
            </div>
          </div>
        </ModalBackdrop>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

export default function ButlerManagementTab({ butlerName }: Props) {
  return (
    <div className="divide-y divide-border">
      <IdentitySection butlerName={butlerName} />
      <SystemPromptSection butlerName={butlerName} />
      <ToolsSection butlerName={butlerName} />
      <MemoryAccessSection butlerName={butlerName} />
      <ActivityStripeSection butlerName={butlerName} />
      <KillSwitchSection butlerName={butlerName} />
    </div>
  );
}
