// ---------------------------------------------------------------------------
// ButlerDetailActions
//
// Composes the Page shell `actions` slot for the Butler detail page.
//
//   status pill | command bar (prompt + complexity + Run) | Logs | Config |
//   Chat | pause/resume button
//
// Command bar (bu-86c4c.18, JARVIS audit move 13): Force Run, the Trigger
// tab, and the ChatPanel "Prompt" button used to be three separate names for
// "make this butler run". They are unified into one prompt-first control:
//   - Empty input + Run (or Enter) fires the default scheduler prompt, same
//     as the old Force Run button.
//   - A custom prompt + Run replaces the old Trigger tab (which also let the
//     operator pick a complexity tier).
//   - Chat remains a distinct, clearly-labeled affordance for a persisted
//     multi-turn conversation (a materially different backend concept --
//     conversations are cross-butler dashboard threads, not one-shot
//     session triggers -- so it is not folded into the same control).
//
// Status pill:    derived from butler.status (ok/degraded/down/error/unknown)
// Pause/Resume:   sets eligibility to "quarantined" (pause) or "active" (resume)
//                 via the Switchboard registry eligibility API. Consequential
//                 (bu-ep4ks.11): scheduled behind the same undo-window
//                 pattern ButlersPage's board established for restore
//                 (bu-86c4c.15) rather than firing on click, since this is
//                 the same eligibility flip.
//
// NO Tier-2 hero block is added — identity stays in the Overview tab card.
// ---------------------------------------------------------------------------

import { type KeyboardEvent, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import { toast } from "sonner";

import { triggerButler } from "@/api/index.ts";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { COMPLEXITY_TIERS, complexityLabel } from "@/components/general/ComplexityBadge.tsx";
import { Button } from "@/components/ui/button";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Time } from "@/components/ui/time";
import { useRegistry, useSetEligibility } from "@/hooks/use-general";
import { UNDO_WINDOW_MS, useUndoWindow } from "@/hooks/use-undo-window";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Fired when the command bar is submitted with an empty prompt (quick-run). */
const DEFAULT_PROMPT = "Run your scheduled tick now.";

/**
 * Default complexity tier. Must be one of the backend's valid tiers
 * (reasoning/workhorse/cheap/specialty/local/legacy -- see
 * model_settings.py:_COMPLEXITY_TIERS); "workhorse" mirrors the backend
 * TriggerRequest default.
 */
const DEFAULT_COMPLEXITY = "workhorse";

const operationalButtonClassName =
  "h-7 rounded-[3px] border-border bg-transparent px-2.5 font-mono text-[10px] font-medium uppercase tracking-[0.06em] shadow-none " +
  "hover:bg-muted/50 hover:text-foreground dark:bg-transparent dark:border-border dark:hover:bg-muted/50";

const primaryOperationalButtonClassName =
  "h-7 rounded-[3px] border-foreground bg-foreground px-2.5 font-mono text-[10px] font-medium uppercase tracking-[0.06em] text-background shadow-none " +
  "hover:bg-foreground/90 hover:text-background dark:border-foreground dark:bg-foreground dark:text-background dark:hover:bg-foreground/90";

function actionLinkClassName(): string {
  return [
    "inline-flex h-7 items-center rounded-[3px] border border-border bg-transparent px-2.5",
    "font-mono text-[10px] font-medium uppercase tracking-[0.06em] text-foreground shadow-none",
    "transition-colors hover:bg-muted/50 hover:text-foreground",
    "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
  ].join(" ");
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ButlerDetailActionsProps {
  butlerName: string;
}

// ---------------------------------------------------------------------------
// ButlerDetailActions
// ---------------------------------------------------------------------------

export function ButlerDetailActions({ butlerName }: ButlerDetailActionsProps) {
  const { data: registryResponse, isLoading: registryLoading } = useRegistry();
  const setEligibility = useSetEligibility();
  const navigate = useNavigate();
  const pauseUndo = useUndoWindow("butler-detail-pause");

  const [prompt, setPrompt] = useState("");
  const [complexity, setComplexity] = useState(DEFAULT_COMPLEXITY);
  const [isRunning, setIsRunning] = useState(false);

  // Find the registry entry to determine current eligibility / paused state.
  // registryEntry is undefined while loading or when the butler is not in the
  // registry; disable the pause control until we have a known state.
  const registryEntry = registryResponse?.data?.find((r) => r.name === butlerName);
  const isPaused = registryEntry?.eligibility_state === "quarantined";
  const pauseScheduled = pauseUndo.isScheduled(butlerName);
  const pauseDisabled =
    registryLoading || registryEntry === undefined || setEligibility.isPending || pauseScheduled;

  // Surface WHY and WHEN a butler was quarantined right at the restore
  // decision point (bu-86c4c.3 — this used to be hidden at the exact moment
  // it matters: an operator deciding whether "Resume" is safe had no idea
  // whether the quarantine was an automated healing action, how long ago it
  // fired, or why — the registry already carries both fields, they just
  // weren't rendered here).
  const quarantineReason = registryEntry?.quarantine_reason ?? null;
  const quarantinedAt = registryEntry?.quarantined_at ?? null;

  async function handleRun() {
    if (isRunning) return;
    const trimmed = prompt.trim();
    setIsRunning(true);
    try {
      const response = await triggerButler(butlerName, trimmed || DEFAULT_PROMPT, complexity);
      toast.success(trimmed ? "Prompt sent" : "Force run triggered");
      // Link the operator straight to the spawned session rather than dropping
      // the returned session_id on the floor.
      if (response.session_id) {
        navigate(`/sessions/${response.session_id}`);
      }
      setPrompt("");
    } catch {
      toast.error("Failed to run butler");
    } finally {
      setIsRunning(false);
    }
  }

  function handleInputKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      void handleRun();
    }
  }

  function handlePauseToggle() {
    if (setEligibility.isPending || pauseScheduled) return;

    const nextState = isPaused ? "active" : "quarantined";
    const verb = isPaused ? "resumed" : "paused";
    const verbing = isPaused ? "Resuming" : "Pausing";

    pauseUndo.schedule(butlerName, () => {
      setEligibility.mutate(
        { name: butlerName, state: nextState },
        {
          onSuccess: () => toast.success(`${butlerName} ${verb}`),
          onError: (err) =>
            toast.error(`Failed to ${isPaused ? "resume" : "pause"} ${butlerName}`, {
              description: err instanceof Error ? err.message : undefined,
            }),
        },
      );
    });

    toast(`${verbing} ${butlerName}`, {
      action: { label: "Undo", onClick: () => pauseUndo.cancel(butlerName) },
      duration: UNDO_WINDOW_MS,
    });
  }

  // Palette verb (bu-t64p2 -- reachability sweep, bu-qvnce.11 slice 5). Reuses
  // this bar's own pause/resume toggle; "Run" is deliberately NOT duplicated
  // here -- GlobalActionsRegistrar already registers "Run <butler>" for
  // every butler (same underlying force-run semantics), so a second "Run
  // <butler>" verb here would be a redundant command-menu entry.
  const pauseResumeCommands = useMemo<PaletteCommand[]>(() => {
    if (pauseDisabled) return [];
    return [
      {
        id: `butler-detail-${isPaused ? "resume" : "pause"}`,
        label: isPaused ? `Resume ${butlerName}` : `Pause ${butlerName}`,
        keywords: ["eligibility", "quarantine", butlerName],
        perform: handlePauseToggle,
      },
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps -- handlePauseToggle is recreated every render and closes over isPaused/butlerName/setEligibility directly; the listed values are what actually vary the resulting command.
  }, [pauseDisabled, isPaused, butlerName]);
  useRegisterCommands(pauseResumeCommands);

  return (
    <div className="flex items-center gap-2" data-testid="butler-detail-actions">
      {/* Unified prompt-first command bar (replaces Force Run + Trigger tab) */}
      <div className="flex items-center gap-1.5" data-testid="butler-command-bar">
        <input
          type="text"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          onKeyDown={handleInputKeyDown}
          placeholder={DEFAULT_PROMPT}
          disabled={isRunning}
          aria-label={`Prompt ${butlerName}`}
          data-testid="butler-command-input"
          className="h-7 w-40 rounded-[3px] border border-border bg-transparent px-2 font-mono text-[11px] text-foreground placeholder:truncate placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring sm:w-56"
        />
        <Select value={complexity} onValueChange={setComplexity} disabled={isRunning}>
          <SelectTrigger
            size="sm"
            data-testid="butler-command-complexity"
            aria-label="Complexity"
            className="h-7 w-auto gap-1 rounded-[3px] border-border bg-transparent px-2 font-mono text-[10px] font-medium uppercase tracking-[0.06em] shadow-none"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {COMPLEXITY_TIERS.map((tier) => (
              <SelectItem key={tier} value={tier} className="font-mono text-xs">
                {complexityLabel(tier)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          size="sm"
          data-testid="butler-force-run"
          disabled={isRunning}
          onClick={handleRun}
          className={primaryOperationalButtonClassName}
        >
          {isRunning ? "Running…" : "Run"}
        </Button>
      </div>

      <Link
        to="?tab=activity&section=logs"
        className={actionLinkClassName()}
        data-testid="butler-logs-link"
      >
        Logs
      </Link>

      <Link
        to="?tab=system&section=config"
        className={actionLinkClassName()}
        data-testid="butler-config-link"
      >
        Config
      </Link>

      <ChatPanel
        butlerName={butlerName}
        triggerClassName={operationalButtonClassName}
        triggerLabel="Chat"
        showTriggerIcon={false}
      />

      {/* Quarantine reason/timestamp — shown right next to the Resume button
          so the operator sees WHY and WHEN before deciding to un-quarantine,
          not just a bare "Resume" affordance. */}
      {isPaused && (quarantineReason || quarantinedAt) && (
        <span
          className="max-w-[280px] truncate font-mono text-[10px] text-muted-foreground"
          title={quarantineReason ?? undefined}
          data-testid="butler-quarantine-info"
        >
          Quarantined
          {quarantinedAt && (
            <>
              {" "}
              <Time value={quarantinedAt} mode="relative" />
            </>
          )}
          {quarantineReason && `: ${quarantineReason}`}
        </span>
      )}

      <Button
        variant={isPaused ? "default" : "outline"}
        size="sm"
        data-testid="butler-pause"
        disabled={pauseDisabled}
        onClick={handlePauseToggle}
        className={isPaused ? operationalButtonClassName : primaryOperationalButtonClassName}
      >
        {setEligibility.isPending || pauseScheduled
          ? isPaused
            ? "Resuming…"
            : "Pausing…"
          : isPaused
            ? "Resume"
            : "Pause"}
      </Button>
    </div>
  );
}
