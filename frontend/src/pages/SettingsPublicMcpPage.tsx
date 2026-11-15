/**
 * SettingsPublicMcpPage — /settings/public-mcp
 *
 * The owner's kill switches for the Public MCP read surface (RFC 0039 D16): the `all` switch
 * and one per domain. Each row shows the state and the time and actor of its last change.
 * Disabling and re-enabling are one action each, with no unlock step.
 * REQ-dashboard-settings-console-001.
 *
 * Honest by construction: a switch only moves when the server confirms (no optimistic flip), a
 * failed write leaves the row as it was and says why, and the copy never claims immediate
 * effect: the change reaches the public server through replication within 15 minutes.
 *
 * Design language: Dispatch. Hairline rows, no card chrome, state is a word first and a dot
 * second, so it never relies on colour alone.
 */

import { useState } from "react";

import type { ControlState, PublicMcpControl } from "@/api/public-mcp-controls";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { InlineActionLink } from "@/components/ui/inline-action-link";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { usePublicMcpControls, useSetPublicMcpControl } from "@/hooks/use-public-mcp-controls";
import { cn } from "@/lib/utils";

const DOMAIN_ORDER = [
  "all",
  "knowledge",
  "relationship",
  "time",
  "travel",
  "finance",
  "health",
  "proposals",
] as const;

const LABEL: Record<string, { name: string; note: string }> = {
  all: { name: "All", note: "Every data tool; describe still answers." },
  knowledge: { name: "Knowledge", note: "Memory facts and search." },
  relationship: { name: "Relationship", note: "People and their records." },
  time: { name: "Time", note: "Episodes and the timeline." },
  travel: { name: "Travel", note: "Trips and legs." },
  finance: { name: "Finance", note: "Ledger and accounts." },
  health: { name: "Health", note: "Measurements and series." },
  proposals: { name: "Proposals", note: "Outside fact proposals and their review." },
};

interface Failure {
  domain: string;
  state: ControlState;
  message: string;
}

function ChangeCell({ control }: { control: PublicMcpControl }) {
  if (control.changed_at === null) {
    return <span className="text-muted-foreground">No change recorded</span>;
  }
  return (
    <span className="text-muted-foreground">
      <Time value={control.changed_at} mode="smart" compact />
      {control.changed_by ? <> by {control.changed_by}</> : null}
    </span>
  );
}

function StateWord({ state }: { state: ControlState | null }) {
  if (state === null) return <span className="text-muted-foreground">No row</span>;
  const off = state === "disabled";
  return (
    <span className="inline-flex items-center gap-2">
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full",
          off ? "bg-[var(--amber-text)]" : "bg-[var(--green-text)]",
        )}
      />
      <span className={cn(off && "text-[var(--amber-text)]")}>{off ? "Disabled" : "Enabled"}</span>
    </span>
  );
}

function Switch({
  name,
  checked,
  disabled,
  pending,
  onToggle,
}: {
  name: string;
  /** "mixed" is a missing row: neither on nor off, so not announced as either. */
  checked: boolean | "mixed";
  /** Not actionable and out of the focus order (loading). */
  disabled: boolean;
  /** A write is in flight: stays focusable, ignores activation. */
  pending: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={`Public MCP ${name}`}
      aria-disabled={pending ? true : undefined}
      disabled={disabled}
      onClick={() => {
        if (!pending) onToggle();
      }}
      className={cn(
        "inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-border px-0.5 transition-colors",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked === true && "justify-end bg-[var(--green-text)]/30",
        checked === false && "justify-start bg-muted",
        checked === "mixed" && "justify-center bg-muted",
        pending && "opacity-50",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "size-3.5 rounded-full",
          checked === "mixed" ? "bg-muted-foreground/50" : "bg-foreground",
        )}
      />
    </button>
  );
}

export default function SettingsPublicMcpPage() {
  const { data, isLoading, isError, refetch } = usePublicMcpControls();
  const write = useSetPublicMcpControl();
  const [failure, setFailure] = useState<Failure | null>(null);
  const [saved, setSaved] = useState<{ domain: string; state: ControlState } | null>(null);

  // isError renders its own branch below, so an absent list is loading, never "no switches".
  const byDomain = new Map((data ? data.data.controls : []).map((c) => [c.domain, c]));
  const loading = isLoading || (!data && !isError);
  const busy = write.isPending;

  function change(domain: string, state: ControlState) {
    setFailure(null);
    setSaved(null);
    write.mutate(
      { domain, state },
      {
        onSuccess: () => setSaved({ domain, state }),
        onError: (error: Error) =>
          setFailure({ domain, state, message: error.message || "The change was refused." }),
      },
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <Eyebrow as="p" className="mb-2">
          settings · public mcp
        </Eyebrow>
        <h1 className="text-3xl font-medium tracking-tight leading-tight">Public MCP</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Switch off what the internet-facing read surface serves, one domain or all of it. Each
          switch is one action; turning it back on is one action too.
        </p>
      </div>

      <section aria-label="How a change takes effect" className="space-y-1 text-sm">
        <p>
          A change reaches the public server through replication within 15 minutes. The
          Relationship proposal importer honours it at its next pass.
        </p>
        <p className="text-muted-foreground">
          For immediate effect, add the domain to the break-glass ConfigMap{" "}
          <code className="font-mono text-xs">butlers-public-mcp-switches</code> (key{" "}
          <code className="font-mono text-xs">disabled_domains</code>, re-read every 10 seconds),
          or scale the public server to zero.
        </p>
        <p className="text-muted-foreground">
          Switching a domain back on here does not clear a break-glass ConfigMap entry; remove it
          from <code className="font-mono text-xs">disabled_domains</code> there too.
        </p>
      </section>

      {failure && (
        <div
          className="attention-row px-4 py-3 flex items-center justify-between gap-4"
          data-tone="red"
          role="alert"
        >
          <p className="text-sm">
            Could not {failure.state === "disabled" ? "disable" : "enable"}{" "}
            {LABEL[failure.domain]?.name ?? failure.domain}: {failure.message}. The switch still
            shows what the server holds.
          </p>
          <InlineActionLink
            onClick={() => {
              if (!busy) change(failure.domain, failure.state);
            }}
          >
            Retry →
          </InlineActionLink>
        </div>
      )}

      {/* Always mounted so assistive technology registers it before its text changes. */}
      <p
        role="status"
        aria-live="polite"
        className={saved && !failure ? "text-sm text-muted-foreground" : "sr-only"}
      >
        {saved && !failure
          ? `${LABEL[saved.domain]?.name ?? saved.domain} is now ${saved.state}. The public server follows by replication within 15 minutes; the break-glass ConfigMap above is the faster path.`
          : ""}
      </p>

      {isError ? (
        <div
          className="attention-row px-4 py-3 flex items-center justify-between"
          data-tone="amber"
          role="alert"
        >
          <p className="text-sm">Could not load the switches.</p>
          <InlineActionLink onClick={() => void refetch()}>Retry →</InlineActionLink>
        </div>
      ) : (
        <ul aria-label="Public MCP switches" aria-busy={loading} className="border-t border-border/60">
          {DOMAIN_ORDER.map((domain) => {
            const control = byDomain.get(domain);
            const label = LABEL[domain];
            const checked: boolean | "mixed" =
              control?.state == null ? "mixed" : control.state === "enabled";
            return (
              <li
                key={domain}
                data-domain={domain}
                className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 border-b border-border/60 py-3 sm:grid-cols-[minmax(0,1.2fr)_8rem_minmax(0,1.6fr)_auto]"
              >
                <div className="min-w-0">
                  <div className="font-medium">{label.name}</div>
                  <div className="text-xs text-muted-foreground">{label.note}</div>
                </div>
                {loading || !control ? (
                  <>
                    <Skeleton className="h-4 w-20" data-testid="switch-skeleton" />
                    <Skeleton className="hidden h-4 w-40 sm:block" />
                  </>
                ) : (
                  <>
                    <div className="text-sm" data-testid={`state-${domain}`}>
                      <StateWord state={control.state} />
                    </div>
                    <div className="col-span-2 text-xs sm:col-span-1">
                      <ChangeCell control={control} />
                      {control.state === null && (
                        <p className="mt-1 text-muted-foreground" data-testid={`no-row-${domain}`}>
                          No row: the public server serves it, because a missing row is not off.
                          The proposal importer treats a missing proposals or all row as off.
                          Switching off creates the row.
                        </p>
                      )}
                    </div>
                  </>
                )}
                <div className="row-start-1 col-start-2 sm:row-start-auto sm:col-start-auto">
                  <Switch
                    name={label.name}
                    checked={checked}
                    disabled={loading || !control}
                    pending={busy}
                    // A missing row is not "disabled": the only safe move from it is to switch off.
                    onToggle={() =>
                      change(domain, control?.state === "disabled" ? "enabled" : "disabled")
                    }
                  />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
