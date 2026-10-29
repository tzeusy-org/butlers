import { useRef, useState } from "react";
import { ApiError } from "@/api/client";
import { useRetryEpisodeConsolidation } from "@/hooks/use-memory";

/** Keyed by the episode: feedback survives pending refresh, but never navigation. */
export function EpisodeRecoveryControls({ episodeId, status, onRefresh }: {
  episodeId: string;
  status: string;
  onRefresh: () => Promise<unknown>;
}) {
  const [message, setMessage] = useState("");
  const outcome = useRef<HTMLParagraphElement>(null);

  return (
    <div className="flex flex-col gap-2">
      {status === "dead_letter" && (
        <RequeueAction
          episodeId={episodeId}
          onRefresh={onRefresh}
          onOutcome={(next) => {
            setMessage(next);
            if (next) outcome.current?.focus({ preventScroll: true });
          }}
        />
      )}
      <p ref={outcome} role="status" aria-live="polite" aria-atomic="true" tabIndex={-1}
        className="text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus">
        {message}
      </p>
    </div>
  );
}

/** Mount the mutation only for a dead letter inside the owner-authorized lane. */
function RequeueAction({ episodeId, onOutcome, onRefresh }: {
  episodeId: string;
  onOutcome: (message: string) => void;
  onRefresh: () => Promise<unknown>;
}) {
  const mutation = useRetryEpisodeConsolidation();
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);

  async function requeue() {
    // Refuse a second activation even before React commits the disabled state.
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    onOutcome("");
    try {
      await mutation.mutateAsync({ episodeId });
      onOutcome("Queued for the next scheduled write-up. No write-up started now.");
      await onRefresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : "The request could not be completed.";
      onOutcome(`Could not queue this episode: ${message}`);
      if (error instanceof ApiError && error.status === 409) await onRefresh();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-start gap-2">
      <button
        type="button"
        disabled={busy}
        aria-busy={busy}
        onClick={() => void requeue()}
        className="rounded-full border border-[var(--border)] px-3.5 py-1 font-mono text-[11px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-40"
      >
        {busy ? "Queueing..." : "Queue for next write-up"}
      </button>
      <p className="text-[13px] text-[var(--mfg)]">Queues one episode for a future scheduled write-up. Nothing runs now.</p>
    </div>
  );
}
