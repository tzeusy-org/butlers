// Words for the QA breaker's cause and the gate behind a halted patrol (bu-ejow5p.3).
// Codes come from the API (core/qa/remediation_liveness.py, core/qa/patrol_status.py).

const CAUSE_LABELS: Record<string, string> = {
  publisher_unconfigured: "no publisher configured",
  proposal_retention_unavailable: "proposal retention unavailable",
  execution_failure: "investigation failures",
  mixed: "mixed causes",
};

const HALT_REASON_LABELS: Record<string, string> = {
  circuit_breaker: "circuit breaker open",
  no_model: "no model configured",
  concurrency_cap: "concurrency cap reached",
};

/** Plain-words cause; an unknown code is shown as its own text, never dropped. */
export function breakerCauseLabel(cause: string | null | undefined): string | null {
  if (!cause) return null;
  return CAUSE_LABELS[cause] ?? cause.replace(/_/g, " ");
}

/** Plain-words gate that halted a patrol; null when the patrol was not halted. */
export function haltReasonLabel(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return HALT_REASON_LABELS[reason] ?? reason.replace(/_/g, " ");
}

export interface BreakerForecast {
  /** True only for a single cause, where "fix it or the next probe fails" is honest. */
  available: boolean;
  text: string;
}

/**
 * What resetting the breaker is likely to do. Withheld on mixed or unknown causes: a forecast
 * over unlike failures would be a guess presented as a finding.
 */
export function breakerResetForecast(cause: string | null | undefined): BreakerForecast {
  if (!cause || cause === "mixed") {
    return {
      available: false,
      text: "No forecast: the failures have different causes, so a reset's outcome can't be predicted.",
    };
  }
  const label = breakerCauseLabel(cause);
  if (cause === "execution_failure") {
    return {
      available: true,
      text: `All counted failures were ${label}. A reset re-admits dispatch; if the underlying fault persists the breaker reopens after five more.`,
    };
  }
  return {
    available: true,
    text: `All counted failures were "${label}". Unless that is fixed first, a reset re-admits dispatch that will fail the same way and reopen the breaker.`,
  };
}
