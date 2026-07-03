// ---------------------------------------------------------------------------
// SystemVerdictBanner -- computed judgment banner for /system (bu-86c4c.17)
//
// Leads the page with a single verdict line ("Instance healthy: v0.4.2, up
// 12d, backed up 3h ago, all 9 beating") or a ranked, clickable problem list
// -- composed client-side from data every tile below already fetches. No new
// endpoint, no LLM cost: the tiles become elaboration, not the message.
//
// Doctrine:
//   - Never asserts "all clear" while any source is still loading/degraded --
//     see stillLoading below.
//   - Every problem line is a door (a real <Link>) when it has a natural
//     drill-down target.
// ---------------------------------------------------------------------------

import { Link } from "react-router";

import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import {
  useBackupFacts,
  useHealthPosture,
  useInsightDeliveryState,
  useInstanceFacts,
} from "@/hooks/use-system";

interface Problem {
  key: string;
  text: string;
  href?: string;
}

function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  if (days >= 1) return `up ${days}d`;
  const hours = Math.floor(seconds / 3600);
  if (hours >= 1) return `up ${hours}h`;
  return `up ${Math.max(1, Math.floor(seconds / 60))}m`;
}

function formatBackupRecency(lastBackupAt: string | null): string {
  if (!lastBackupAt) return "never backed up";
  const backupTime = new Date(lastBackupAt).getTime();
  if (Number.isNaN(backupTime)) return "backup time unknown";
  const ageMs = Date.now() - backupTime;
  const hours = Math.floor(ageMs / (3600 * 1000));
  if (hours < 1) return "backed up <1h ago";
  if (hours < 48) return `backed up ${hours}h ago`;
  return `backed up ${Math.floor(hours / 24)}d ago`;
}

export function SystemVerdictBanner() {
  const { aggregates: board } = useButlerStatusBoard();
  const instance = useInstanceFacts();
  const backups = useBackupFacts();
  const insights = useInsightDeliveryState();
  const posture = useHealthPosture();

  // Never render a verdict (all-clear or otherwise) while a source that
  // feeds it is still loading -- a premature "all clear" is worse than a
  // brief skeleton.
  const stillLoading =
    board.isLoading || instance.isLoading || backups.isLoading || insights.isPending || posture.isPending;

  if (stillLoading) {
    return (
      <div
        className="mb-4 h-12 animate-pulse rounded bg-muted"
        data-testid="verdict-banner-skeleton"
        aria-label="Loading instance verdict"
      />
    );
  }

  const problems: Problem[] = [];

  if (board.offline > 0) {
    problems.push({
      key: "offline",
      text: `${board.offline} butler${board.offline === 1 ? "" : "s"} offline`,
      href: "/butlers",
    });
  }
  if (board.quarantined > 0) {
    problems.push({
      key: "quarantined",
      text: `${board.quarantined} quarantined`,
      href: "/butlers",
    });
  }
  if (board.overdue > 0) {
    problems.push({
      key: "overdue",
      text: `${board.overdue} overdue against their own schedule`,
      href: "/butlers",
    });
  }

  const insightData = insights.data?.data;
  if (insightData && insightData.failed > 0) {
    problems.push({
      key: "insights",
      text: `${insightData.failed} insight${insightData.failed === 1 ? "" : "s"} failed to deliver`,
    });
  }

  const backupData = backups.data?.data;
  if (backupData && !backupData.backup_source_reachable) {
    problems.push({ key: "backup-unreachable", text: "backup source unreachable" });
  } else if (backupData && !backupData.last_backup_at) {
    problems.push({ key: "backup-never", text: "never backed up" });
  }

  const postureData = posture.data;
  const insecure: string[] = [];
  if (postureData?.security?.insecure_infra_defaults) insecure.push("insecure infra defaults");
  if (postureData?.security?.role_enforcement_disabled) insecure.push("DB role enforcement disabled");
  if (insecure.length > 0) {
    problems.push({ key: "posture", text: insecure.join(", ") });
  }

  if (board.sourcesPartiallyDegraded) {
    problems.push({ key: "degraded", text: "some fleet data is degraded or unavailable" });
  }

  if (problems.length === 0) {
    const instanceData = instance.data?.data;
    const parts = [
      instanceData ? `v${instanceData.version}` : null,
      instanceData ? formatUptime(instanceData.uptime_seconds) : null,
      backupData ? formatBackupRecency(backupData.last_backup_at) : null,
      board.total > 0 ? `all ${board.total} beating` : null,
    ].filter((p): p is string => Boolean(p));

    return (
      <div
        role="status"
        data-testid="verdict-banner-all-clear"
        className="mb-4 rounded border border-border px-4 py-3 font-mono text-sm text-muted-foreground"
      >
        Instance healthy{parts.length > 0 ? `: ${parts.join(", ")}` : ""}
      </div>
    );
  }

  return (
    <div
      role="group"
      aria-label="Instance problems"
      data-testid="verdict-banner-problems"
      className="mb-4 rounded border border-amber-500/40 bg-amber-500/5 px-4 py-3"
    >
      <span className="mb-2 block font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        {problems.length} {problems.length === 1 ? "thing needs" : "things need"} you
      </span>
      <ul className="flex flex-col gap-1 text-sm">
        {problems.map((p) =>
          p.href ? (
            <li key={p.key}>
              <Link to={p.href} className="text-inherit hover:underline">
                {p.text}
              </Link>
            </li>
          ) : (
            <li key={p.key}>{p.text}</li>
          ),
        )}
      </ul>
    </div>
  );
}
