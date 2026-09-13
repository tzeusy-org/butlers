import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { getUnroutableAttention, retryUnroutableAttention } from "@/api/index.ts";
import { SourceDegradedNote } from "@/components/ui/query-boundary.tsx";
import { Time } from "@/components/ui/time.tsx";

export function UnroutableAttentionPanel() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["approvals", "unroutable"],
    queryFn: getUnroutableAttention,
  });
  const retry = useMutation({
    mutationFn: retryUnroutableAttention,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["approvals", "unroutable"] });
      toast.success("Message queued for routing retry");
    },
    onError: (error: Error) => toast.error(error.message || "Could not retry routing"),
  });

  if (query.isError) {
    return (
      <div className="px-6 py-3 border-b border-border">
        <SourceDegradedNote
          label="Unroutable messages"
          onRetry={() => void query.refetch()}
          testId="unroutable-attention-degraded"
        />
      </div>
    );
  }

  const rows = query.data?.data;
  if (!rows || rows.length === 0) return null;

  return (
    <section className="px-6 py-3 border-b border-border" aria-labelledby="unroutable-heading">
      <h2
        id="unroutable-heading"
        className="mb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground"
      >
        Unroutable messages
      </h2>
      <ul className="divide-y divide-border/60" data-testid="unroutable-attention-list">
        {rows.map((row) => {
          const retrying = retry.isPending && retry.variables === row.id;
          return (
            <li key={row.id} className="attention-row py-2.5 pl-3" data-tone="red">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-sm break-words">Unroutable: {row.question}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground break-words">
                    {row.failure_reason}
                  </p>
                  <span className="mt-1 block font-mono text-[10px] text-muted-foreground tnum">
                    <Time value={row.created_at} mode="relative-compact" />
                  </span>
                </div>
                <button
                  type="button"
                  disabled={retrying}
                  aria-label={`${retrying ? "Retrying" : "Retry"} unroutable message`}
                  onClick={() => retry.mutate(row.id)}
                  className="shrink-0 font-mono text-[11px] underline underline-offset-2 hover:text-foreground disabled:opacity-50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                  {retrying ? "retrying…" : "Retry"}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
