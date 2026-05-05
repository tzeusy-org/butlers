import { Link, useParams } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Time } from "@/components/ui/time";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { permanenceBadge, PercentageProgressBar } from "@/components/memory/badges";
import { DetailPage } from "@/components/layout/DetailPage";
import { useFact } from "@/hooks/use-memory";

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

export default function FactDetailPage() {
  const { factId } = useParams<{ factId: string }>();
  const { data, isLoading, error } = useFact(factId ?? null);
  const fact = data?.data;

  const breadcrumbs = [
    { label: "Memory", href: "/memory" },
    { label: "Facts", href: "/memory?tab=facts" },
    { label: fact?.subject ?? factId ?? "Fact" },
  ];

  const title = fact?.subject ?? factId ?? "Fact";
  const subtitle = fact?.predicate;

  return (
    <DetailPage
      record={{ title, subtitle, type: "fact" }}
      breadcrumbs={breadcrumbs}
      loading={isLoading}
      error={error ?? null}
      pulse={null}
      primary={
        fact ? (
          <Card>
            <CardHeader>
              <CardTitle>
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
                    {fact.object_entity_id ? (
                      <Link
                        to={`/entities/${fact.object_entity_id}`}
                        className="text-primary hover:underline"
                      >
                        {fact.object_entity_name ?? fact.content}
                      </Link>
                    ) : (
                      fact.content
                    )}
                  </p>
                </div>
              </div>

              {/* Status row */}
              <div className="flex flex-wrap items-center gap-4">
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
                  Created: <Time value={fact.created_at} mode="absolute" />
                </span>
                {fact.last_referenced_at && (
                  <span>
                    Last referenced:{" "}
                    <Time value={fact.last_referenced_at} mode="absolute" />
                  </span>
                )}
                {fact.last_confirmed_at && (
                  <span>
                    Last confirmed:{" "}
                    <Time value={fact.last_confirmed_at} mode="absolute" />
                  </span>
                )}
              </div>
            </CardContent>
          </Card>
        ) : null
      }
      supporting={
        fact ? (
          <>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Confidence</CardTitle>
              </CardHeader>
              <CardContent>
                <PercentageProgressBar value={fact.confidence} label="Confidence" />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Permanence</CardTitle>
              </CardHeader>
              <CardContent>
                {permanenceBadge(fact.permanence)}
              </CardContent>
            </Card>
          </>
        ) : null
      }
      auxiliary={null}
      practical={null}
    />
  );
}
