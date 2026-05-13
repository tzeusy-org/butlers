// ---------------------------------------------------------------------------
// ButlerDetailActions
//
// Composes the Page shell `actions` slot for the Butler detail page per the
// Gate-A A2 resolution (bu-rx6c2):
//
//   status pill | force-run button | pause/resume button | ChatPanel button
//
// Status pill:    derived from butler.status (ok/degraded/down/error/unknown)
// Force-run:      calls triggerButler with a default scheduler prompt
// Pause/Resume:   sets eligibility to "quarantined" (pause) or "active" (resume)
//                 via the Switchboard registry eligibility API
//
// NO Tier-2 hero block is added — identity stays in the Overview tab card.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { toast } from "sonner";

import { triggerButler } from "@/api/index.ts";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { Button } from "@/components/ui/button";
import { useButler } from "@/hooks/use-butlers";
import { useRegistry, useSetEligibility } from "@/hooks/use-general";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

type DetailMode = "operator" | "resident";

function statusLabel(status: string): string {
  switch (status) {
    case "ok":
    case "healthy":
      return "online";
    case "degraded":
      return "degraded";
    case "error":
    case "down":
      return "down";
    default:
      return status || "unknown";
  }
}

function statusToneClass(status: string): string {
  switch (status) {
    case "ok":
    case "healthy":
      return "bg-emerald-500";
    case "degraded":
      return "bg-amber-500";
    case "error":
    case "down":
      return "bg-destructive";
    default:
      return "bg-muted-foreground";
  }
}

const operationalButtonClassName =
  "h-7 rounded-[3px] border-border bg-transparent px-2.5 font-mono text-[10px] font-medium uppercase tracking-[0.06em] shadow-none " +
  "hover:bg-muted/50 hover:text-foreground dark:bg-transparent dark:border-border dark:hover:bg-muted/50";

interface ButlerDetailActionsProps {
  butlerName: string;
  /** Current operator/resident view mode. */
  mode: DetailMode;
  /** Callback to change the mode. */
  onModeChange: (mode: DetailMode) => void;
}

// ---------------------------------------------------------------------------
// ButlerDetailActions
// ---------------------------------------------------------------------------

export function ButlerDetailActions({
  butlerName,
  mode,
  onModeChange,
}: ButlerDetailActionsProps) {
  const { data: butlerResponse } = useButler(butlerName);
  const { data: registryResponse, isLoading: registryLoading } = useRegistry();
  const setEligibility = useSetEligibility();

  const [isForceRunning, setIsForceRunning] = useState(false);

  const butler = butlerResponse?.data;
  const status = butler?.status ?? "unknown";

  // Find the registry entry to determine current eligibility / paused state.
  // registryEntry is undefined while loading or when the butler is not in the
  // registry; disable the pause control until we have a known state.
  const registryEntry = registryResponse?.data?.find((r) => r.name === butlerName);
  const isPaused = registryEntry?.eligibility_state === "quarantined";
  const pauseDisabled = registryLoading || registryEntry === undefined || setEligibility.isPending;

  async function handleForceRun() {
    if (isForceRunning) return;
    setIsForceRunning(true);
    try {
      await triggerButler(butlerName, "Run your scheduled tick now.", "medium");
      toast.success("Force run triggered");
    } catch {
      toast.error("Failed to trigger force run");
    } finally {
      setIsForceRunning(false);
    }
  }

  function handlePauseToggle() {
    if (setEligibility.isPending) return;
    setEligibility.mutate({
      name: butlerName,
      state: isPaused ? "active" : "quarantined",
    });
  }

  function handleModeToggle() {
    onModeChange(mode === "operator" ? "resident" : "operator");
  }

  return (
    <div className="flex items-center gap-2" data-testid="butler-detail-actions">
      <span
        data-testid="butler-status-pill"
        aria-label={`Butler status: ${statusLabel(status)}`}
        className="inline-flex items-center gap-1.5 font-mono text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground"
      >
        <span
          className={`h-1.5 w-1.5 rounded-full ${statusToneClass(status)}`}
          aria-hidden="true"
        />
        {statusLabel(status)}
      </span>

      {/* Operator / Resident mode toggle pill */}
      <button
        type="button"
        role="switch"
        aria-checked={mode === "operator"}
        aria-label={`Switch to ${mode === "operator" ? "resident" : "operator"} mode`}
        data-testid="butler-mode-toggle"
        onClick={handleModeToggle}
        className="inline-flex h-7 cursor-pointer select-none items-center rounded-[3px] border border-border bg-transparent px-2.5 font-mono text-[10px] font-medium uppercase tracking-[0.06em] text-foreground transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      >
        {mode === "operator" ? "Operator" : "Resident"}
      </button>

      <Button
        variant="outline"
        size="sm"
        data-testid="butler-force-run"
        disabled={isForceRunning}
        onClick={handleForceRun}
        className={operationalButtonClassName}
      >
        {isForceRunning ? "Running…" : "Force Run"}
      </Button>

      <Button
        variant={isPaused ? "default" : "outline"}
        size="sm"
        data-testid="butler-pause"
        disabled={pauseDisabled}
        onClick={handlePauseToggle}
        className={operationalButtonClassName}
      >
        {setEligibility.isPending
          ? isPaused
            ? "Resuming…"
            : "Pausing…"
          : isPaused
            ? "Resume"
            : "Pause"}
      </Button>

      <ChatPanel butlerName={butlerName} triggerClassName={operationalButtonClassName} />
    </div>
  );
}
