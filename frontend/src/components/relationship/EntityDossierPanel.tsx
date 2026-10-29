/**
 * Graph dossier for one entity: what the fleet knows, by source (bu-8cdl1.8,
 * REQ-entity-graph-011).  Renders the dossier API's actual receipts and its known/withheld
 * counts; withheld relationships are a count only, so there is nothing to reveal.  Loading,
 * successful empty and unavailable (with a read-only retry) are distinct states.
 */

import { Link } from "react-router";

import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { useEntityDossier } from "@/hooks/use-entity-dossier.ts";

function relationshipsLabel(count: number): string {
  return `${count} ${count === 1 ? "relationship" : "relationships"}`;
}

export function EntityDossierPanel({ entityId }: { entityId: string }) {
  const { data: dossier, isLoading, isError, refetch } = useEntityDossier(entityId);

  let body;
  if (isLoading) {
    body = (
      <div data-testid="entity-dossier-loading" aria-busy="true" className="space-y-2">
        <p className="text-muted-foreground text-xs">Loading the relationship dossier</p>
        <Skeleton className="h-8 w-full" />
      </div>
    );
  } else if (isError || !dossier) {
    body = (
      <SourceDegradedNote
        testId="entity-dossier-error"
        label="Relationship dossier"
        onRetry={() => void refetch()}
      />
    );
  } else if (dossier.relationships_known === 0 && dossier.relationships_withheld === 0) {
    body = (
      <p className="text-muted-foreground text-sm" data-testid="entity-dossier-empty">
        No relationships are recorded for this person yet.
      </p>
    );
  } else {
    body = (
      <div className="space-y-3">
        <p className="text-sm" data-testid="entity-dossier-coverage">
          {relationshipsLabel(dossier.relationships_known)} known
          {dossier.relationships_withheld > 0
            ? `, ${dossier.relationships_withheld} withheld for sensitivity`
            : ""}
        </p>
        {dossier.sources.map((source) => (
          <div
            key={`${source.source_schema}/${source.source_table}`}
            data-testid="entity-dossier-source"
            className="space-y-1.5"
          >
            <div className="flex items-baseline gap-2">
              <h4 className="font-mono text-xs">
                {source.source_schema}.{source.source_table}
              </h4>
              <span className="text-muted-foreground text-xs">{source.live_count}</span>
            </div>
            <ul className="space-y-1">
              {source.receipts.map((receipt) => (
                <li key={receipt.edge_id} className="flex items-baseline gap-2 text-sm">
                  <span className="text-muted-foreground text-xs">
                    {receipt.direction === "subject" ? "to" : "from"}
                  </span>
                  <Link to={`/entities/${receipt.counterpart_entity_id}`} className="underline">
                    {receipt.counterpart_name ?? receipt.counterpart_entity_id}
                  </Link>
                  <span className="text-muted-foreground">{receipt.predicate}</span>
                  <span className="text-muted-foreground ml-auto text-xs tabular-nums">
                    <Time value={receipt.updated_at} mode="absolute" precision="day" />
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
        {dossier.receipts_truncated ? (
          <p className="text-muted-foreground text-xs" data-testid="entity-dossier-truncated">
            Showing the newest {dossier.receipts_limit} relationships.
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <section className="space-y-2" data-testid="entity-dossier-panel">
      <h3 className="text-sm font-semibold uppercase tracking-wide">Relationship dossier</h3>
      {body}
    </section>
  );
}
