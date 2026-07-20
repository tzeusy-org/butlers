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

import { DispatchVerdict, type VerdictClause } from "@/components/ui/dispatch-verdict";
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import {
  useBackupFacts,
  useDeploymentFacts,
  useDriftFacts,
  useHealthPosture,
  useInsightDeliveryState,
  useInstanceFacts,
} from "@/hooks/use-system";

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

function bindMountedWorktreeTruth(current: {
  source: string | null;
  serving_mode: string | null;
  serving_worktree: string | null;
}): string | null {
  if (current.serving_mode !== "hotreload-worktree") return null;

  const actor = current.source === "boot" ? "boot" : "serving";
  const worktree = current.serving_worktree ? ` ${current.serving_worktree}` : "";
  return `${actor} from bind-mounted worktree${worktree} (hotreload)`;
}

export function SystemVerdictBanner() {
  const { aggregates: board } = useButlerStatusBoard();
  const instance = useInstanceFacts();
  const backups = useBackupFacts();
  const insights = useInsightDeliveryState();
  const posture = useHealthPosture();
  const drift = useDriftFacts();
  const deployments = useDeploymentFacts();

  const sources = [
    { label: "fleet status", isLoading: board.isLoading, isError: board.isError, href: "/butlers" },
    { label: "instance facts", isLoading: instance.isLoading, isError: instance.isError },
    { label: "backup facts", isLoading: backups.isLoading, isError: backups.isError },
    { label: "insight delivery status", isLoading: insights.isPending, isError: insights.isError },
    { label: "security posture", isLoading: posture.isPending, isError: posture.isError },
    { label: "migration drift status", isLoading: drift.isPending, isError: drift.isError },
    { label: "deployment status", isLoading: deployments.isPending, isError: deployments.isError },
  ];

  const problems: VerdictClause[] = [];

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

  // bu-9r3hd.5: a reachable, recently-run backup used to read as an
  // unconditional all-clear. It no longer does -- a corrupt/empty artifact,
  // a stale backup, or a failed/never-run restore drill are each their own
  // real problem, surfaced the same way "never backed up" already was.
  const backupData = backups.data?.data;
  if (backupData && !backupData.backup_source_reachable) {
    problems.push({ key: "backup-unreachable", text: "backup source unreachable" });
  } else if (backupData && !backupData.last_backup_at) {
    problems.push({ key: "backup-never", text: "never backed up" });
  } else if (backupData) {
    if (backupData.last_backup_status === "corrupt" || backupData.last_backup_status === "empty") {
      problems.push({
        key: "backup-artifact",
        text: `backup artifact ${backupData.last_backup_status}`,
      });
    }
    if (backupData.backup_stale) {
      problems.push({ key: "backup-stale", text: "backup is stale" });
    }
    const drill = backupData.restore_drill;
    if (drill?.result === "fail") {
      problems.push({ key: "restore-drill-fail", text: "restore drill failed" });
    } else if (drill?.result === "degraded") {
      problems.push({ key: "restore-drill-degraded", text: "restore drill status unavailable" });
    } else if (!drill || drill.result === "pending") {
      problems.push({ key: "restore-drill-pending", text: "restore drill never run" });
    }
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

  const driftData = drift.data?.data;
  if (driftData && !driftData.drift_check_available) {
    problems.push({ key: "drift-unavailable", text: "migration drift check unavailable" });
  } else if (driftData?.is_drifted) {
    const chainCount = driftData.drifted.length;
    problems.push({
      key: "drift",
      text: driftData.escalated
        ? `${chainCount} migration chain${chainCount === 1 ? "" : "s"} drifted, escalated to QA`
        : `${chainCount} migration chain${chainCount === 1 ? "" : "s"} drifted`,
    });
  }

  // bu-hmdqz.1: the live instance was silently frozen 16+ merges behind
  // origin/main with nothing on /system saying so -- deploy failures and
  // "N commits behind" both need to be as loud as migration drift.
  const deploymentData = deployments.data?.data;
  const worktreeTruth = deploymentData?.current
    ? bindMountedWorktreeTruth(deploymentData.current)
    : null;
  if (worktreeTruth) {
    problems.push({
      key: "bind-mounted-worktree",
      text: worktreeTruth,
      className: "text-[var(--red-text)] font-medium",
    });
  }
  if (deploymentData?.current?.result === "failed") {
    problems.push({ key: "deploy-failed", text: "last deploy failed" });
  }
  if (deploymentData && !deploymentData.commits_behind_available) {
    problems.push({ key: "commits-behind-unavailable", text: "commits-behind-origin/main check unavailable" });
  } else if (deploymentData && (deploymentData.commits_behind_main ?? 0) > 0) {
    const n = deploymentData.commits_behind_main as number;
    problems.push({
      key: "commits-behind",
      text: `serving ${n} commit${n === 1 ? "" : "s"} behind origin/main`,
    });
  }

  const instanceData = instance.data?.data;
  const parts = [
    instanceData ? `v${instanceData.version}` : null,
    instanceData ? formatUptime(instanceData.uptime_seconds) : null,
    backupData ? formatBackupRecency(backupData.last_backup_at) : null,
    board.total > 0 ? `all ${board.total} beating` : null,
  ].filter((p): p is string => Boolean(p));
  const problemCount = sources.filter((source) => source.isError).length + problems.length;

  return (
    <DispatchVerdict
      testId="system"
      landmarkLabel="instance verdict"
      sources={sources}
      clauses={problems}
      allClear={`Instance healthy${parts.length > 0 ? `: ${parts.join(", ")}` : ""}`}
      clausesLabel="Instance problems"
      layout="stacked"
      className="mb-4"
      skeletonClassName="h-12"
      allClearClassName="rounded border border-border px-4 py-3"
      clausesClassName="rounded border border-[var(--amber)]/40 bg-[var(--amber)]/5 px-4 py-3"
      clausesHeader={
        <span className="mb-2 block font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          {problemCount} {problemCount === 1 ? "thing needs" : "things need"} you
        </span>
      }
    />
  );
}
