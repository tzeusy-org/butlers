/**
 * ReembedPanel — Embedding migration admin panel.
 *
 * Displays per-tier stale-embedding counts and provides Dry-run / Run
 * re-embed actions.  The Run action is destructive (writes to DB) so it
 * requires an explicit confirmation modal before proceeding.
 *
 * Abort: the backend POST /api/memory/reembed is a synchronous, blocking
 * call with no server-side cancellation support.  Aborting the fetch would
 * orphan the server-side work.  An abort button is omitted; document this
 * as future work if the backend adds request-cancellation.
 */

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useReembedPending, useReembedRun } from "@/hooks/use-memory-reembed";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ReembedPanelProps {
  /** Optional butler name to scope the panel to a single butler. */
  butler?: string;
}

// ---------------------------------------------------------------------------
// Tier label map
// ---------------------------------------------------------------------------

const TIER_LABELS: Record<string, string> = {
  episodes: "Episodes (Eden)",
  facts: "Facts (Mid-term)",
  rules: "Rules (Long-term)",
};

function tierLabel(tier: string): string {
  return TIER_LABELS[tier] ?? tier;
}

// ---------------------------------------------------------------------------
// Confirmation modal
// ---------------------------------------------------------------------------

interface ConfirmModalProps {
  open: boolean;
  butler: string;
  onConfirm: () => void;
  onCancel: () => void;
}

function ConfirmModal({ open, butler, onConfirm, onCancel }: ConfirmModalProps) {
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Run re-embed?</DialogTitle>
          <DialogDescription>
            This will re-embed all stale rows for butler{" "}
            <span className="font-mono font-semibold">{butler}</span>. DB writes
            will be performed. This operation can take several minutes and
            cannot be aborted once started.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm}>
            Confirm re-embed
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// ReembedPanel
// ---------------------------------------------------------------------------

export default function ReembedPanel({ butler }: ReembedPanelProps) {
  const { data: pendingResp, isLoading } = useReembedPending(butler);
  const reembedMutation = useReembedRun();

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [lastResult, setLastResult] = useState<{
    dry_run: boolean;
    total: number;
    tiers_processed: string[];
    counts: Record<string, number>;
    errors: string[];
  } | null>(null);

  const pending = pendingResp?.data;

  function handleDryRun() {
    if (!pending || !butler) return;
    setLastResult(null);
    reembedMutation.mutate(
      {
        butler,
        dry_run: true,
        current_model: pending.current_model,
      },
      {
        onSuccess: (resp) => {
          setLastResult(resp.data);
        },
      },
    );
  }

  function handleRunConfirmed() {
    setConfirmOpen(false);
    if (!pending || !butler) return;
    setLastResult(null);
    reembedMutation.mutate(
      {
        butler,
        dry_run: false,
        current_model: pending.current_model,
      },
      {
        onSuccess: (resp) => {
          setLastResult(resp.data);
        },
      },
    );
  }

  const isPending = reembedMutation.isPending;
  const isError = reembedMutation.isError;

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Embedding migration</CardTitle>
          <CardDescription>
            Rows whose stored embedding model differs from the currently
            configured model are counted as stale. Use Dry-run to estimate scope
            before committing.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Pending counts table */}
          {isLoading && (
            <p className="text-muted-foreground text-sm">Loading pending counts…</p>
          )}
          {pending && !isLoading && (
            <div>
              <p className="text-muted-foreground mb-2 text-xs">
                Model:{" "}
                <span className="font-mono">{pending.current_model}</span>
              </p>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="pb-2 pr-4">Tier</th>
                    <th className="pb-2 text-right">Stale rows</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(pending.counts).map(([tier, count]) => (
                    <tr key={tier} className="border-b last:border-0">
                      <td className="py-1.5 pr-4">{tierLabel(tier)}</td>
                      <td className="py-1.5 text-right font-mono">
                        {count.toLocaleString()}
                      </td>
                    </tr>
                  ))}
                  <tr>
                    <td className="pt-2 pr-4 font-semibold">Total</td>
                    <td className="pt-2 text-right font-mono font-semibold">
                      {pending.total.toLocaleString()}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}

          {/* Actions */}
          {!butler && pending && (
            <p className="text-muted-foreground text-xs">
              No butler selected — actions are disabled.
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={isPending || !pending || !butler}
              onClick={handleDryRun}
            >
              {isPending && reembedMutation.variables?.dry_run
                ? "Running dry-run…"
                : "Dry-run"}
            </Button>
            <Button
              variant="destructive"
              size="sm"
              disabled={isPending || !pending || !butler}
              onClick={() => setConfirmOpen(true)}
            >
              {isPending && !reembedMutation.variables?.dry_run
                ? "Re-embedding… (this may take several minutes)"
                : "Run re-embed"}
            </Button>
          </div>

          {/* Result */}
          {lastResult && (
            <div className="rounded border bg-muted/30 p-3 text-sm">
              <p className="font-semibold">
                {lastResult.dry_run ? "Dry-run result" : "Re-embed complete"}
              </p>
              <p className="text-muted-foreground mt-1">
                {lastResult.dry_run ? "Would process" : "Processed"}{" "}
                <span className="font-mono font-medium">
                  {lastResult.total.toLocaleString()}
                </span>{" "}
                rows across {lastResult.tiers_processed.map(tierLabel).join(", ")}.
              </p>
              {Object.keys(lastResult.counts).length > 0 && (
                <ul className="mt-1 list-inside list-disc text-xs text-muted-foreground">
                  {Object.entries(lastResult.counts).map(([tier, count]) => (
                    <li key={tier}>
                      {tierLabel(tier)}: {count.toLocaleString()}
                    </li>
                  ))}
                </ul>
              )}
              {lastResult.errors.length > 0 && (
                <p className="text-destructive mt-2 text-xs">
                  {lastResult.errors.length} error(s):{" "}
                  {lastResult.errors.join("; ")}
                </p>
              )}
            </div>
          )}

          {/* Error state */}
          {isError && (
            <p className="text-destructive text-sm">
              Re-embed failed. Check the console for details.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Confirmation modal */}
      <ConfirmModal
        open={confirmOpen}
        butler={butler ?? "(default)"}
        onConfirm={handleRunConfirmed}
        onCancel={() => setConfirmOpen(false)}
      />
    </>
  );
}
