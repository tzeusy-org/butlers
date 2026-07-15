// ---------------------------------------------------------------------------
// MemoryLoadError — the /memory house-ledger's load-failure line (bu-mkd5r)
//
// The error counterpart to every band's serif-italic empty line ("Nothing
// waiting.", "The ledger is empty.", "No sweeps recorded."). An errored query
// on this page must render THIS, never fall through to one of those calm empty
// lines — a killed backend must not read as a genuinely quiet house
// (three-way loading/error/empty state contract, JARVIS audit move 1b).
//
// Restrained by construction to fit the band aesthetic (no card, no banner
// chrome): a serif-italic line in the readable red-text variant plus an
// underlined mono `retry` word, mirroring HousekeepingBand's existing
// "save failed, try again" / "re-embed failed, try again" idiom. role="alert"
// so a degraded source announces itself to assistive tech.
// ---------------------------------------------------------------------------

import { Voice } from "@/components/ui/Voice";
import { cn } from "@/lib/utils";

export interface MemoryLoadErrorProps {
  /** What failed to load, e.g. "the ledger" or "attention". Reads as "Couldn't load {label}." */
  label: string;
  onRetry?: () => void;
  className?: string;
  /** Optional test id so a page-level test can assert the error (not empty) state renders. */
  testId?: string;
}

export function MemoryLoadError({ label, onRetry, className, testId }: MemoryLoadErrorProps) {
  return (
    <div
      role="alert"
      data-testid={testId}
      className={cn("flex flex-wrap items-baseline gap-x-3 gap-y-1 py-4", className)}
    >
      <Voice variant="italic" as="span" className="text-[var(--red-text)]">
        Couldn&rsquo;t load {label}.
      </Voice>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className={cn(
            "font-mono text-[11px] leading-[1.4] text-[var(--red-text)]",
            "underline [text-underline-offset:4px] transition-colors hover:text-fg",
          )}
        >
          retry
        </button>
      )}
    </div>
  );
}
