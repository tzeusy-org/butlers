// ---------------------------------------------------------------------------
// ModelAllowanceStrip -- provider allowance per account on the Models tab
// [bu-q7vx1q.50]. Read-only: no reset control, no probe.
// ---------------------------------------------------------------------------

import { useEffect, useState } from "react";

import type { AllowanceObservation } from "@/api/types.ts";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { formatAllowanceCountdown, readAllowance } from "@/lib/allowance";

interface ModelAllowanceStripProps {
  data: AllowanceObservation | undefined;
  isError: boolean;
  isForbidden?: boolean;
  /** Test seam; defaults to Date.now. */
  now?: () => number;
}

export function ModelAllowanceStrip({
  data,
  isError,
  isForbidden = false,
  now = Date.now,
}: ModelAllowanceStripProps) {
  const [nowMs, setNowMs] = useState(() => now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(now()), 30_000);
    return () => clearInterval(id);
  }, [now]);

  if (isForbidden) return null;
  if (isError || (data && !data.available)) {
    return (
      <SourceDegradedNote
        label="Allowance"
        detail="allowance unknown"
        className="mx-7 my-2"
        testId="models-allowance-unknown"
      />
    );
  }
  if (!data || data.accounts.length === 0) return null;

  const rows = data.accounts.map((account) => ({
    account,
    reading: readAllowance(account, nowMs),
  }));
  const availableCount = rows.filter((r) => r.reading.kind === "available").length;
  const flagged = rows.filter((r) => r.reading.kind !== "available");

  return (
    <div
      className="px-7 py-2.5 border-b border-border font-mono text-xs text-foreground flex items-center gap-4 flex-wrap"
      role="status"
      data-testid="models-allowance"
    >
      <span className="text-[9px] uppercase tracking-widest text-muted-foreground">allowance</span>
      {flagged.map(({ account, reading }) => (
        <span key={account.account_key} data-testid={`models-allowance-${account.account_key}`}>
          {account.account_key}:{" "}
          {reading.kind === "countdown" ? (
            <>
              resets in {formatAllowanceCountdown(reading.msRemaining)} ({reading.label})
            </>
          ) : reading.kind === "should-be-back" ? (
            "should be back"
          ) : reading.kind === "exhausted-no-reset" ? (
            "exhausted, reset unknown"
          ) : (
            "unknown"
          )}
        </span>
      ))}
      {availableCount > 0 && (
        <span className="text-muted-foreground" data-testid="models-allowance-available">
          {availableCount} available
        </span>
      )}
    </div>
  );
}
