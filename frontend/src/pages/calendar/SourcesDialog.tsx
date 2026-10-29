/**
 * "Configure Sources" dialog: connected accounts with health, and each
 * calendar source with its sync toggle, view-only hide, primary selection and
 * sync/recover actions.
 */

import { useMemo } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import type { CalendarWorkspaceSourceFreshness } from "@/api/types.ts";
import { useSetPrimaryCalendar } from "@/hooks/use-calendar-workspace";
import { ButlerMark } from "@/components/ui/ButlerMark";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Mono } from "@/components/ui/Mono";
import { Row } from "@/components/ui/Row";
import { StateDot } from "@/components/ui/StateDot";
import { Tip } from "@/components/ui/tip";
import { Voice } from "@/components/ui/Voice";
import { cn } from "@/lib/utils";
import { KindTag, PillButton } from "@/pages/calendar/atoms";
import { formatOptionalTimestamp, formatStaleness } from "@/pages/calendar/freshness";
import type { CalendarSync } from "@/pages/calendar/use-calendar-sync";
import type { SourceControls } from "@/pages/calendar/use-source-controls";
import {
  accountHealthDotState,
  sourceName,
  syncDotState,
  truncateCalendarId,
} from "@/pages/calendar/workspace-model";

interface SourcesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connectedSources: CalendarWorkspaceSourceFreshness[];
  metaLoading: boolean;
  primaryCalendarId: string | null;
  controls: SourceControls;
  sync: CalendarSync;
}

export function SourcesDialog({
  open,
  onOpenChange,
  connectedSources,
  metaLoading,
  primaryCalendarId,
  controls,
  sync,
}: SourcesDialogProps) {
  const primaryMutation = useSetPrimaryCalendar();
  const { accountsQuery, disabledSources, hiddenSources, toggleSourceEnabled, toggleSourceHidden } =
    controls;
  const { syncMutation, syncingSourceKey, handleSyncSource } = sync;

  // Collect the set of Google account emails from source metadata.
  // The backend stores `account_email` on each source during calendar discovery.
  const googleAccountEmails = useMemo(() => {
    const emails = new Set<string>();
    for (const source of connectedSources) {
      const email = source.metadata?.account_email;
      if (typeof email === "string" && email) {
        emails.add(email);
      }
    }
    return emails;
  }, [connectedSources]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[90vw] max-w-[90vw] sm:w-[80vw] sm:max-w-[80vw] max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Configure Sources</DialogTitle>
          <DialogDescription>
            Connected Google accounts and their calendars. The checkbox
            enables or disables a calendar as a sync source (persisted); a
            disabled calendar is skipped by sync but its already-synced events
            stay visible. Use Hide/Show to remove a calendar from the grid
            without changing its sync, a view-only preference.
          </DialogDescription>
        </DialogHeader>
        {(() => {
          const accounts = accountsQuery.data?.data.accounts ?? [];
          const healthAvailable =
            accountsQuery.data?.data.health_available ?? true;
          if (accounts.length === 0) return null;
          return (
            <div
              className="mb-3 space-y-1.5"
              role="list"
              aria-label="Connected accounts"
            >
              <Mono muted className="text-[11px] uppercase tracking-wide">
                Accounts
              </Mono>
              {accounts.map((account) => (
                <Row
                  key={account.account_id}
                  meta={
                    <span className="inline-flex items-center gap-1.5">
                      <StateDot
                        state={accountHealthDotState(account.health.state)}
                      />
                      <Mono muted>
                        {healthAvailable
                          ? account.health.state
                          : "health unavailable"}
                      </Mono>
                    </span>
                  }
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      className="truncate text-sm font-medium text-fg"
                      title={
                        account.email ??
                        account.display_name ??
                        account.account_id
                      }
                    >
                      {account.email ||
                        account.display_name ||
                        account.account_id}
                    </span>
                    {account.is_primary ? (
                      <KindTag className="text-fg">primary</KindTag>
                    ) : null}
                    <KindTag>{account.status}</KindTag>
                    {account.health.error_message ? (
                      <span
                        className="max-w-[16rem] truncate text-[11px] text-[var(--red-text)]"
                        title={account.health.error_message}
                      >
                        {account.health.error_message}
                      </span>
                    ) : null}
                  </div>
                </Row>
              ))}
            </div>
          );
        })()}
        {metaLoading && connectedSources.length === 0 ? (
          <Voice variant="italic" className="text-[var(--mfg)]">
            Reading source metadata…
          </Voice>
        ) : connectedSources.length === 0 ? (
          <Voice variant="italic" className="text-[var(--mfg)]">
            No connected sources.
          </Voice>
        ) : (
          <div role="list">
            {[...connectedSources]
              .sort((a, b) => {
                // Sort: primary first, then user-email calendars, then enabled, then disabled
                const aPrimary =
                  a.lane === "user" && a.calendar_id === primaryCalendarId
                    ? 1
                    : 0;
                const bPrimary =
                  b.lane === "user" && b.calendar_id === primaryCalendarId
                    ? 1
                    : 0;
                if (aPrimary !== bPrimary) return bPrimary - aPrimary;

                const aIsAcct =
                  a.provider === "google" &&
                  a.calendar_id &&
                  googleAccountEmails.has(a.calendar_id)
                    ? 1
                    : 0;
                const bIsAcct =
                  b.provider === "google" &&
                  b.calendar_id &&
                  googleAccountEmails.has(b.calendar_id)
                    ? 1
                    : 0;
                if (aIsAcct !== bIsAcct) return bIsAcct - aIsAcct;

                const aOff = disabledSources.has(a.source_key) ? 1 : 0;
                const bOff = disabledSources.has(b.source_key) ? 1 : 0;
                return aOff - bOff;
              })
              .map((source) => {
                const isPrimary =
                  source.lane === "user" &&
                  source.calendar_id != null &&
                  source.calendar_id === primaryCalendarId;
                const canSetPrimary =
                  source.lane === "user" &&
                  source.writable &&
                  source.calendar_id != null &&
                  source.calendar_id !== primaryCalendarId &&
                  source.butler_name != null;
                const isEnabled = !disabledSources.has(source.source_key);
                const isHidden = hiddenSources.has(source.source_key);
                const acctEmail =
                  typeof source.metadata?.account_email === "string"
                    ? source.metadata.account_email
                    : undefined;
                const calIdDisplay = (() => {
                  if (
                    acctEmail &&
                    source.calendar_id &&
                    source.calendar_id !== acctEmail
                  ) {
                    return `${acctEmail} ${truncateCalendarId(source.calendar_id)}`;
                  }
                  return truncateCalendarId(
                    source.calendar_id ??
                      source.provider ??
                      source.source_kind,
                  );
                })();

                return (
                  <Row
                    key={source.source_key}
                    className={cn((!isEnabled || isHidden) && "opacity-50")}
                    mark={
                      <Checkbox
                        checked={isEnabled}
                        onCheckedChange={() => toggleSourceEnabled(source)}
                        aria-label={`Toggle ${sourceName(source)}`}
                      />
                    }
                    meta={
                      <div className="flex items-center gap-1.5">
                        <Tip
                          content={
                            isHidden
                              ? "Hidden from the grid (view-only; sync unaffected)"
                              : "Hide from the grid (view-only; sync unaffected)"
                          }
                        >
                          <PillButton
                            onClick={() => toggleSourceHidden(source)}
                            aria-label={`${isHidden ? "Show" : "Hide"} ${sourceName(source)} in view`}
                            aria-pressed={isHidden}
                          >
                            {isHidden ? "Show" : "Hide"}
                          </PillButton>
                        </Tip>
                        {canSetPrimary ? (
                          <PillButton
                            onClick={() => {
                              primaryMutation.mutate(
                                {
                                  butler_name: source.butler_name!,
                                  calendar_id: source.calendar_id!,
                                },
                                {
                                  onSuccess: (response) => {
                                    if (response.data.persisted === false) {
                                      toast.error(
                                        "Failed to set primary: change was not persisted",
                                      );
                                      return;
                                    }
                                    toast.success("Primary calendar updated");
                                  },
                                  onError: (err) =>
                                    toast.error(
                                      `Failed to set primary: ${err.message}`,
                                    ),
                                },
                              );
                            }}
                            disabled={primaryMutation.isPending}
                          >
                            Set as primary
                          </PillButton>
                        ) : null}
                        <PillButton
                          onClick={() => handleSyncSource(source)}
                          disabled={
                            syncMutation.isPending || !source.butler_name
                          }
                        >
                          {syncingSourceKey === source.source_key
                            ? "Syncing..."
                            : "Sync now"}
                        </PillButton>
                        <PillButton
                          onClick={() =>
                            handleSyncSource(source, { full: true })
                          }
                          disabled={
                            syncMutation.isPending || !source.butler_name
                          }
                          title="Full re-sync from scratch (cursor recovery)"
                        >
                          Recover
                        </PillButton>
                        {source.error_kind === "token_expired" ||
                        source.error_kind === "auth" ? (
                          <Link
                            to="/ingestion?tab=connectors"
                            className="inline-flex items-center rounded-[3px] border border-[var(--red)] px-2 py-0.5 text-[11px] font-medium text-[var(--red-text)] transition-colors hover:bg-[var(--red)]/10"
                            title="This source needs re-authorization"
                          >
                            Reconnect
                          </Link>
                        ) : null}
                      </div>
                    }
                  >
                    <div className="flex min-w-0 flex-col gap-1">
                      <div className="flex min-w-0 items-center gap-2">
                        {source.butler_name ? (
                          <ButlerMark name={source.butler_name} />
                        ) : null}
                        <span
                          className="truncate text-sm font-medium text-fg"
                          title={sourceName(source)}
                        >
                          {sourceName(source)}
                        </span>
                        {isPrimary ? (
                          <KindTag className="text-fg">
                            primary
                          </KindTag>
                        ) : null}
                        <KindTag>{source.lane}</KindTag>
                      </div>
                      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                        <span
                          className="min-w-0 max-w-full truncate"
                          title={source.calendar_id ?? undefined}
                        >
                          <Mono muted>{calIdDisplay}</Mono>
                        </span>
                        <span className="inline-flex items-center gap-1.5">
                          <StateDot state={syncDotState(source.sync_state)} />
                          <Mono muted>{source.sync_state}</Mono>
                        </span>
                        <Mono muted>
                          {formatStaleness(source.staleness_ms)}
                          {formatOptionalTimestamp(source.last_success_at)
                            ? ` \u00b7 ${formatOptionalTimestamp(source.last_success_at)}`
                            : ""}
                        </Mono>
                        {source.last_error ? (
                          <span
                            className="inline-flex min-w-0 items-center gap-1.5"
                            title={source.last_error}
                          >
                            <StateDot state="error" />
                            {source.error_kind &&
                            source.error_kind !== "none" ? (
                              <KindTag className="text-[var(--red-text)]">
                                {source.error_kind}
                              </KindTag>
                            ) : null}
                            <span className="max-w-[16rem] truncate text-[11px] text-[var(--red-text)]">
                              {source.last_error}
                            </span>
                          </span>
                        ) : null}
                      </div>
                    </div>
                  </Row>
                );
              })}
          </div>
        )}
        <DialogFooter>
          <PillButton onClick={() => onOpenChange(false)}>
            Close
          </PillButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
