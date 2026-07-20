import type { QaPatrolReadStatus, QaPatrolStatus } from "@/api/types";

export type QaPatrolStatusTone = "healthy" | "attention" | "destructive" | "muted";

export interface QaPatrolStatusPresentation {
  label: string;
  dotClassName: string;
  tone: QaPatrolStatusTone;
}

const PATROL_STATUS_PRESENTATIONS: Record<QaPatrolStatus, QaPatrolStatusPresentation> = {
  clean: { label: "clean", dotClassName: "bg-[var(--green)]", tone: "healthy" },
  findings_dispatched: {
    label: "findings dispatched",
    dotClassName: "bg-[var(--amber)]",
    tone: "attention",
  },
  suppressed: {
    label: "findings suppressed",
    dotClassName: "bg-[var(--amber)]",
    tone: "attention",
  },
  error: { label: "patrol error", dotClassName: "bg-destructive", tone: "destructive" },
  running: { label: "patrol running", dotClassName: "bg-muted-foreground", tone: "muted" },
  skipped_overlap: {
    label: "patrol skipped due to overlap",
    dotClassName: "bg-muted-foreground",
    tone: "muted",
  },
};

const UNKNOWN_PATROL_STATUS_PRESENTATION: QaPatrolStatusPresentation = {
  label: "unknown patrol status",
  dotClassName: "bg-destructive",
  tone: "destructive",
};

/**
 * Return a total, fail-closed presentation for a status read from QA patrols.
 *
 * The response can contain a corrupt or newer persisted value; never let that
 * value inherit the healthy presentation used only by ``clean``.
 */
export function getQaPatrolStatusPresentation(
  status: QaPatrolReadStatus,
): QaPatrolStatusPresentation {
  if (Object.prototype.hasOwnProperty.call(PATROL_STATUS_PRESENTATIONS, status)) {
    return PATROL_STATUS_PRESENTATIONS[status as QaPatrolStatus];
  }
  return UNKNOWN_PATROL_STATUS_PRESENTATION;
}
