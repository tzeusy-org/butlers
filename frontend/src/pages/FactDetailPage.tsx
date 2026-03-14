import { Link, useParams } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useFact } from "@/hooks/use-memory";

function permanenceBadge(p: string) {
  const colors: Record<string, string> = {
    permanent: "bg-blue-600 text-white hover:bg-blue-600/90",
    stable: "bg-sky-600 text-white hover:bg-sky-600/90",
    standard: "",
    volatile: "border-amber-500 text-amber-600",
    ephemeral: "border-red-500 text-red-500",
  };
  const cls = colors[p];
  if (!cls) return <Badge variant="secondary">{p}</Badge>;
  if (cls.startsWith("border-"))
    return (
      <Badge variant="outline" className={cls}>
        {p}
      </Badge>
    );
  return <Badge className={cls}>{p}</Badge>;
}

function validityBadge(v: string) {
  switch (v) {
    case "active":
      return (
        <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">
          active
        </Badge>
      );
    case "fading":
      return (
        <Badge variant="outline" className="border-amber-500 text-amber-600">
          fading
        </Badge>
      );
    case "superseded":
      return <Badge variant="secondary">superseded</Badge>;
    case "expired":
      return <Badge variant="destructive">expired</Badge>;
    default:
      return <Badge variant="secondary">{v}</Badge>;
  }
}

function confidenceBar(value: number) {
  const pct = Math.round(value * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="bg-muted h-2 w-24 overflow-hidden rounded-full">
        <div
          className="bg-primary h-full rounded-full"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-muted-foreground text-sm">{pct}%</span>
    </div>
  );
}

export default function FactDetailPage() {
  const { factId } = useParams<{ factId: string }>();
  const { data, isLoading, error } = useFact(factId ?? null);
  const fact = data?.data;

  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[
          { label: "Memory", href: "/memory" },
          { label: "Facts", href: "/memory?tab=facts" },
          { label: fact?.entity_name ?? fact?.subject ?? factId ?? "Fact" },
        ]}
      />

      {isLoading && (
        <div className="space-y-4">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      )}

      {error && (
        <div className="text-destructive py-12 text-center text-sm">
          Failed to load fact. {(error as Error).message}
        </div>
      )}

      {fact && (
        <>
          {/* Header */}
          <Card>
            <CardHeader>
              <CardTitle className="text-2xl">
                {fact.entity_id ? (
                  <Link
                    to={`/entities/${fact.entity_id}`}
                    className="text-primary hover:underline"
                  >
                    {fact.entity_name ?? fact.subject}
                  </Link>
                ) : (
                  fact.subject
                )}
              </CardTitle>
              <p className="text-muted-foreground text-sm">{fact.predicate}</p>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Content */}
              <div>
                <p className="text-muted-foreground mb-1 text-sm font-medium">
                  Content
                </p>
                <div className="rounded-md bg-muted/30 p-4">
                  <p className="text-sm whitespace-pre-wrap break-words">
                    {fact.content}
                  </p>
                </div>
              </div>

              {/* Status row */}
              <div className="flex flex-wrap items-center gap-4">
                <div>
                  <p className="text-muted-foreground mb-1 text-xs font-medium">
                    Confidence
                  </p>
                  {confidenceBar(fact.confidence)}
                </div>
                <div>
                  <p className="text-muted-foreground mb-1 text-xs font-medium">
                    Permanence
                  </p>
                  {permanenceBadge(fact.permanence)}
                </div>
                <div>
                  <p className="text-muted-foreground mb-1 text-xs font-medium">
                    Validity
                  </p>
                  {validityBadge(fact.validity)}
                </div>
                <div>
                  <p className="text-muted-foreground mb-1 text-xs font-medium">
                    Scope
                  </p>
                  <Badge variant="outline">{fact.scope}</Badge>
                </div>
              </div>

              {/* Metrics */}
              <div className="flex gap-6 text-sm">
                <div>
                  <span className="text-muted-foreground">Decay rate: </span>
                  <span className="tabular-nums">{fact.decay_rate}</span>
                </div>
                <div>
                  <span className="text-muted-foreground">
                    Reference count:{" "}
                  </span>
                  <span className="tabular-nums">{fact.reference_count}</span>
                </div>
              </div>

              {/* Provenance */}
              <div className="space-y-1 text-sm">
                <p className="text-muted-foreground text-xs font-medium">
                  Provenance
                </p>
                <div className="flex flex-wrap gap-4">
                  {fact.source_butler && (
                    <div>
                      <span className="text-muted-foreground">
                        Source butler:{" "}
                      </span>
                      <Badge variant="outline">{fact.source_butler}</Badge>
                    </div>
                  )}
                  {fact.source_episode_id && (
                    <div>
                      <span className="text-muted-foreground">
                        Source episode:{" "}
                      </span>
                      <Link
                        to={`/memory/episodes/${fact.source_episode_id}`}
                        className="text-primary hover:underline"
                      >
                        {fact.source_episode_id}
                      </Link>
                    </div>
                  )}
                  {fact.supersedes_id && (
                    <div>
                      <span className="text-muted-foreground">
                        Supersedes:{" "}
                      </span>
                      <Link
                        to={`/memory/facts/${fact.supersedes_id}`}
                        className="text-primary hover:underline"
                      >
                        {fact.supersedes_id}
                      </Link>
                    </div>
                  )}
                  {!fact.source_butler &&
                    !fact.source_episode_id &&
                    !fact.supersedes_id && (
                      <span className="text-muted-foreground">
                        No provenance data.
                      </span>
                    )}
                </div>
              </div>

              {/* Tags */}
              {fact.tags.length > 0 && (
                <div>
                  <p className="text-muted-foreground mb-1 text-xs font-medium">
                    Tags
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {fact.tags.map((tag) => (
                      <Badge key={tag} variant="secondary">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Metadata */}
              {Object.keys(fact.metadata).length > 0 && (
                <div>
                  <p className="text-muted-foreground mb-1 text-xs font-medium">
                    Metadata
                  </p>
                  <pre className="rounded-md bg-muted p-3 text-xs overflow-x-auto">
                    {JSON.stringify(fact.metadata, null, 2)}
                  </pre>
                </div>
              )}

              {/* Timestamps */}
              <div className="flex flex-wrap gap-6 text-xs text-muted-foreground">
                <span>
                  Created: {new Date(fact.created_at).toLocaleString()}
                </span>
                {fact.last_referenced_at && (
                  <span>
                    Last referenced:{" "}
                    {new Date(fact.last_referenced_at).toLocaleString()}
                  </span>
                )}
                {fact.last_confirmed_at && (
                  <span>
                    Last confirmed:{" "}
                    {new Date(fact.last_confirmed_at).toLocaleString()}
                  </span>
                )}
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
