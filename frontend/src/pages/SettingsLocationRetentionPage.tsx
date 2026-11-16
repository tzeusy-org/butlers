/**
 * SettingsLocationRetentionPage: /settings/location-retention
 *
 * The owner's control over raw OwnTracks location evidence (REQ-chronicler-api-018,
 * bu-s11n0s.75): the horizon (1..30 days) and the single switch that turns raw expiry on.
 * Both are written by the policy's compare-and-set PUT; a horizon change keeps the switch.
 *
 * Honest by construction: nothing moves before the server answers (no optimistic flip), every
 * write that turns expiry on or off, or shortens the horizon while it is on, is confirmed
 * first, a refused write leaves the page showing what the server holds and refetches, and the
 * copy never claims evidence was already forgotten: deletion follows on the hourly job.
 *
 * Design language: Dispatch, as on /settings/public-mcp. Hairline rows, state is a word first
 * and a dot second.
 */

import { useState } from "react";

import { ApiError } from "@/api/client";
import type { LocationRetentionPolicy, LocationRetentionUpdate } from "@/api/location-retention";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { InlineActionLink } from "@/components/ui/inline-action-link";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { useLocationRetention, useSetLocationRetention } from "@/hooks/use-location-retention";
import { cn } from "@/lib/utils";

/**
 * What the owner is confirming, frozen when the dialog opens: the version and horizon they saw.
 * A background refetch while the dialog is open must not retarget the write at a newer version
 * (the compare-and-set then refuses it instead of applying a change the owner never saw).
 */
type Pending =
  | { kind: "switch"; enforced: boolean; days: number; version: number }
  | { kind: "shorten"; days: number; version: number };

function parseDays(raw: string, policy: LocationRetentionPolicy): number | null {
  if (!/^\d+$/.test(raw.trim())) return null;
  const days = Number(raw.trim());
  return days >= policy.min_days && days <= policy.max_days ? days : null;
}

function failureText(error: Error): string {
  if (error instanceof ApiError && error.status === 409) {
    return "The policy changed elsewhere; the page now shows the current one. Check it and try again.";
  }
  return `The change was refused: ${error.message || "unknown error"}. The page still shows what the server holds.`;
}

function StateWord({ enforced }: { enforced: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full",
          enforced ? "bg-[var(--green-text)]" : "bg-[var(--amber-text)]",
        )}
      />
      <span className={cn(!enforced && "text-[var(--amber-text)]")}>{enforced ? "On" : "Off"}</span>
    </span>
  );
}

function ExpirySwitch({
  checked,
  pending,
  onToggle,
}: {
  checked: boolean;
  pending: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label="Raw location expiry"
      aria-disabled={pending ? true : undefined}
      onClick={() => {
        if (!pending) onToggle();
      }}
      className={cn(
        "inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-border px-0.5 transition-colors",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        checked ? "justify-end bg-[var(--green-text)]/30" : "justify-start bg-muted",
        pending && "opacity-50",
      )}
    >
      <span aria-hidden className="size-3.5 rounded-full bg-foreground" />
    </button>
  );
}

function confirmCopy(pending: Pending, policy: LocationRetentionPolicy) {
  if (pending.kind === "shorten") {
    return {
      title: `Shorten the horizon to ${pending.days} days?`,
      label: `Shorten to ${pending.days} days`,
      variant: "destructive" as const,
      body: `Expiry is on, so raw fixes older than ${pending.days} days become eligible for deletion at the next hourly run. Deleted fixes cannot be undone; summaries keep ${policy.precision_after_forgetting}.`,
    };
  }
  if (pending.enforced) {
    return {
      title: "Turn raw location expiry on?",
      label: "Turn expiry on",
      variant: "destructive" as const,
      body: `From the next hourly run, raw OwnTracks fixes older than ${pending.days} days that every projection has finished with are deleted, along with Chronicler's and Switchboard's copies of them. This cannot be undone; summaries keep ${policy.precision_after_forgetting}.`,
    };
  }
  return {
    title: "Turn raw location expiry off?",
    label: "Turn expiry off",
    variant: "default" as const,
    body: "Nothing new is prepared or offered for deletion, and raw fixes are kept from now on. A grant the connector already fetched may still complete.",
  };
}

export default function SettingsLocationRetentionPage() {
  const { data: policy, isLoading, isError, refetch } = useLocationRetention();
  const write = useSetLocationRetention();
  // The owner's typing, tied to the version it was typed against: a new server copy resets it.
  const [typed, setTyped] = useState<{ version: number; value: string } | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const draft =
    policy && typed && typed.version === policy.version
      ? typed.value
      : policy
        ? String(policy.days)
        : "";

  const busy = write.isPending;

  function send(update: LocationRetentionUpdate, done: string) {
    setFailure(null);
    setSaved(null);
    write.mutate(update, {
      onSuccess: () => setSaved(done),
      onError: (error: Error) => setFailure(failureText(error)),
      onSettled: () => setPending(null),
    });
  }

  function confirm() {
    if (!policy || !pending) return;
    if (pending.kind === "switch") {
      send(
        { days: pending.days, expected_version: pending.version, enforced: pending.enforced },
        pending.enforced
          ? "Expiry is on. The hourly job starts deleting eligible raw fixes at its next run."
          : "Expiry is off. Raw fixes are kept from now on.",
      );
    } else {
      send(
        { days: pending.days, expected_version: pending.version },
        `The horizon is now ${pending.days} days.`,
      );
    }
  }

  function saveHorizon() {
    if (!policy) return;
    const days = parseDays(draft, policy);
    if (days === null || days === policy.days) return;
    if (policy.enforced && days < policy.days) {
      setPending({ kind: "shorten", days, version: policy.version });
      return;
    }
    send({ days, expected_version: policy.version }, `The horizon is now ${days} days.`);
  }

  const draftDays = policy ? parseDays(draft, policy) : null;
  const canSave = !!policy && !busy && draftDays !== null && draftDays !== policy.days;
  const copy = policy && pending ? confirmCopy(pending, policy) : null;

  return (
    <div className="space-y-6">
      <div>
        <Eyebrow as="p" className="mb-2">
          settings · location retention
        </Eyebrow>
        <h1 className="text-3xl font-medium tracking-tight leading-tight">Location retention</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          How long raw OwnTracks fixes are kept, and whether they are deleted once that time has
          passed.
        </p>
      </div>

      {failure && (
        <div className="attention-row px-4 py-3" data-tone="red" role="alert">
          <p className="text-sm">{failure}</p>
        </div>
      )}

      {/* Always mounted so assistive technology registers it before its text changes. */}
      <p
        role="status"
        aria-live="polite"
        className={saved && !failure ? "text-sm text-muted-foreground" : "sr-only"}
      >
        {saved && !failure ? saved : ""}
      </p>

      {isError ? (
        <div
          className="attention-row px-4 py-3 flex items-center justify-between"
          data-tone="amber"
          role="alert"
        >
          <p className="text-sm">Could not load the retention policy.</p>
          <InlineActionLink onClick={() => void refetch()}>Retry →</InlineActionLink>
        </div>
      ) : isLoading || !policy ? (
        <div aria-busy className="space-y-3 border-t border-border/60 pt-3">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="h-5 w-64" />
        </div>
      ) : (
        <>
          <section aria-label="Expiry" className="border-t border-border/60">
            <div className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 border-b border-border/60 py-3">
              <div className="min-w-0">
                <div className="font-medium">
                  Raw expiry{" "}
                  <span className="text-sm font-normal" data-testid="expiry-state">
                    <StateWord enforced={policy.enforced} />
                  </span>
                </div>
                <p className="text-xs text-muted-foreground" data-testid="expiry-effect">
                  {policy.enforced
                    ? `Raw fixes older than ${policy.days} days are deleted by an hourly job once every projection has finished with them.`
                    : "Raw fixes are kept indefinitely. Nothing is deleted until you turn expiry on."}
                </p>
              </div>
              <ExpirySwitch
                checked={policy.enforced}
                pending={busy}
                onToggle={() =>
                  setPending({
                    kind: "switch",
                    enforced: !policy.enforced,
                    days: policy.days,
                    version: policy.version,
                  })
                }
              />
            </div>

            <form
              className="grid grid-cols-[1fr_auto] items-end gap-x-4 gap-y-1 border-b border-border/60 py-3"
              onSubmit={(event) => {
                event.preventDefault();
                saveHorizon();
              }}
            >
              <div className="min-w-0 space-y-1">
                <label htmlFor="retention-days" className="font-medium block">
                  Horizon in days
                </label>
                <p className="text-xs text-muted-foreground" id="retention-days-note">
                  {policy.min_days} to {policy.max_days} days. A fix's age is its{" "}
                  {policy.raw_age_basis}.
                </p>
                <Input
                  id="retention-days"
                  inputMode="numeric"
                  className="w-24"
                  aria-describedby="retention-days-note"
                  aria-invalid={draft !== "" && draftDays === null ? true : undefined}
                  value={draft}
                  onChange={(event) =>
                    policy && setTyped({ version: policy.version, value: event.target.value })
                  }
                />
              </div>
              <Button type="submit" size="sm" disabled={!canSave}>
                Save horizon
              </Button>
            </form>
          </section>

          <section aria-label="What forgetting means" className="space-y-1 text-sm">
            <p>
              After deletion, place, movement and away summaries keep{" "}
              {policy.precision_after_forgetting}.
            </p>
            <p className="text-muted-foreground">
              Covers{" "}
              {policy.covered_sources.map((source, index) => (
                <span key={source}>
                  {index > 0 && ", "}
                  <code className="font-mono text-xs">{source}</code>
                </span>
              ))}
              .
            </p>
            <p className="text-muted-foreground">
              Last changed <Time value={policy.updated_at} mode="smart" compact />, version{" "}
              {policy.version}.
            </p>
          </section>
        </>
      )}

      {copy && (
        <ConfirmDialog
          open
          onOpenChange={(open) => {
            if (!open && !busy) setPending(null);
          }}
          title={copy.title}
          description={copy.body}
          confirmLabel={copy.label}
          pendingLabel="Saving…"
          variant={copy.variant}
          pending={busy}
          onConfirm={confirm}
        />
      )}
    </div>
  );
}
