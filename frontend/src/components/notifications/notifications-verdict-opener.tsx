// ---------------------------------------------------------------------------
// NotificationsVerdictOpener -- /notifications page opener (bu-y0v0c, JARVIS
// pursuit move 9, slice 3)
//
// Composes a windowed verdict from GET /api/notifications/stats: "N failed
// notifications in the last 24h; M from <butler>" -- via the shared
// DispatchVerdict primitive. by_butler on that response was fetched but
// discarded entirely (notification-stats-bar.tsx:90-99 only ever rendered
// by_channel); it is now scoped to FAILED notifications specifically (see
// NotificationStats.by_butler's doc comment) so this clause reads as a true
// breakdown of the failures already being reported, not an unrelated
// all-status count sitting next to them.
//
// source_available === false (the Switchboard notifications source is
// genuinely unreachable) is folded into the isError-suppression contract
// alongside the ordinary react-query isError -- a 200 response with all-zero
// counts must never render as a truthful "no failures" all-clear.
// ---------------------------------------------------------------------------

import type { NotificationStats } from "@/api/index.ts";
import { DispatchVerdict, type VerdictClause } from "@/components/ui/dispatch-verdict";

/** Lookback window for the windowed verdict (matches the Overview page's
 * DEFAULT_RECENT_ISSUE_HOURS convention for "recent" windows). */
export const NOTIFICATIONS_VERDICT_WINDOW_HOURS = 24;

function buildClauses(stats: NotificationStats): VerdictClause[] {
  const clauses: VerdictClause[] = [];

  if (stats.failed > 0) {
    clauses.push({
      key: "failed",
      text: `${stats.failed} failed notification${stats.failed === 1 ? "" : "s"} in the last ${NOTIFICATIONS_VERDICT_WINDOW_HOURS}h`,
      href: "/notifications?status=failed",
    });

    const topButler = Object.entries(stats.by_butler).sort(([, a], [, b]) => b - a)[0];
    if (topButler) {
      const [butler, count] = topButler;
      clauses.push({
        key: "top-butler",
        text: `${count} from ${butler}`,
        href: `/notifications?status=failed&butler=${encodeURIComponent(butler)}`,
      });
    }
  }

  return clauses;
}

function buildAllClear(stats: NotificationStats): string {
  const base = `No failed notifications in the last ${NOTIFICATIONS_VERDICT_WINDOW_HOURS}h`;
  return stats.sent > 0 ? `${base} (${stats.sent.toLocaleString()} sent).` : `${base}.`;
}

export interface NotificationsVerdictOpenerProps {
  /** GET /api/notifications/stats?since=<24h ago> */
  stats: NotificationStats | undefined;
  isLoading: boolean;
  /** react-query isError -- a genuine request failure (network/5xx). */
  isError: boolean;
}

export function NotificationsVerdictOpener({
  stats,
  isLoading,
  isError,
}: NotificationsVerdictOpenerProps) {
  // source_available === false means the Switchboard pool was unreachable --
  // a 200 with all-zero counts, not a truthful "nothing failed" (classify-
  // before-flagging: contrast with a genuinely empty notifications table,
  // which is a legitimate all-clear).
  const sourceUnavailable = stats?.source_available === false;
  const clauses = stats && !sourceUnavailable ? buildClauses(stats) : [];

  return (
    <DispatchVerdict
      testId="notifications"
      landmarkLabel="Notifications verdict"
      sources={[
        { label: "notification stats", isLoading, isError: isError || sourceUnavailable },
      ]}
      clauses={clauses}
      allClear={stats ? buildAllClear(stats) : "No failed notifications."}
    />
  );
}
