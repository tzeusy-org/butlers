import { Link, useParams } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Time } from "@/components/ui/time";
import { Card, CardContent } from "@/components/ui/card";
import { permanenceBadge, PercentageProgressBar } from "@/components/memory/badges";
import { DetailPage } from "@/components/layout/DetailPage";
import { useRule } from "@/hooks/use-memory";

function maturityBadge(m: string) {
  switch (m) {
    case "proven":
      return (
        <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">
          proven
        </Badge>
      );
    case "established":
      return (
        <Badge className="bg-sky-600 text-white hover:bg-sky-600/90">
          established
        </Badge>
      );
    case "candidate":
      return <Badge variant="secondary">candidate</Badge>;
    case "anti_pattern":
      return <Badge variant="destructive">anti-pattern</Badge>;
    default:
      return <Badge variant="secondary">{m}</Badge>;
  }
}

export default function RuleDetailPage() {
  const { ruleId } = useParams<{ ruleId: string }>();
  const { data, isLoading, error } = useRule(ruleId ?? null);
  const rule = data?.data;

  const breadcrumbs = [
    { label: "Memory", href: "/memory" },
    { label: "Rules", href: "/memory?register=rules" },
    { label: "Rule" },
  ];

  // Use the rule content as the title; fall back to the ID while loading.
  // Truncate to 80 chars with ellipsis per shell title spec.
  const truncateTitle = (content: string | undefined): string => {
    if (content == null) return ruleId ?? "Rule";
    return content.length > 80 ? content.slice(0, 79) + "…" : content;
  };
  const title = truncateTitle(rule?.content);

  return (
    <DetailPage
      record={{ title, type: "rule" }}
      breadcrumbs={breadcrumbs}
      loading={isLoading}
      error={error ?? null}
      pulse={null}
      primary={
        rule ? (
          <Card>
            <CardContent className="space-y-4 pt-6">
              {/* Content */}
              <div>
                <p className="text-muted-foreground mb-1 text-sm font-medium">
                  Content
                </p>
                <div className="rounded-md bg-muted/30 p-4">
                  <p className="text-sm whitespace-pre-wrap break-words">
                    {rule.content}
                  </p>
                </div>
              </div>

              {/* Status row */}
              <div className="flex flex-wrap items-center gap-4">
                <div>
                  <p className="text-muted-foreground mb-1 text-xs font-medium">
                    Maturity
                  </p>
                  {maturityBadge(rule.maturity)}
                </div>
                <div>
                  <p className="text-muted-foreground mb-1 text-xs font-medium">
                    Scope
                  </p>
                  <Badge variant="outline">{rule.scope}</Badge>
                </div>
                <div>
                  <p className="text-muted-foreground mb-1 text-xs font-medium">
                    Permanence
                  </p>
                  {permanenceBadge(rule.permanence)}
                </div>
              </div>

              {/* Effectiveness */}
              <div className="flex flex-wrap items-end gap-6">
                <PercentageProgressBar value={rule.effectiveness_score} label="Effectiveness" />
                <div className="text-sm">
                  <span className="text-muted-foreground">Applied: </span>
                  <span className="tabular-nums">{rule.applied_count}</span>
                </div>
                <div className="text-sm">
                  <span className="text-muted-foreground">Successes: </span>
                  <span className="tabular-nums">{rule.success_count}</span>
                </div>
                <div className="text-sm">
                  <span className="text-muted-foreground">Harmful: </span>
                  <span className="tabular-nums">{rule.harmful_count}</span>
                </div>
              </div>

              {/* Confidence */}
              <div className="flex flex-wrap items-end gap-6">
                <PercentageProgressBar value={rule.confidence} label="Confidence" />
                <div className="text-sm">
                  <span className="text-muted-foreground">Decay rate: </span>
                  <span className="tabular-nums">{rule.decay_rate}</span>
                </div>
              </div>

              {/* Provenance */}
              <div className="space-y-1 text-sm">
                <p className="text-muted-foreground text-xs font-medium">
                  Provenance
                </p>
                <div className="flex flex-wrap gap-4">
                  {rule.source_butler && (
                    <div>
                      <span className="text-muted-foreground">
                        Source butler:{" "}
                      </span>
                      <Badge variant="outline">{rule.source_butler}</Badge>
                    </div>
                  )}
                  {rule.source_episode_id && (
                    <div>
                      <span className="text-muted-foreground">
                        Source episode:{" "}
                      </span>
                      <Link
                        to={`/memory/episodes/${rule.source_episode_id}`}
                        className="text-primary hover:underline"
                      >
                        {rule.source_episode_id}
                      </Link>
                    </div>
                  )}
                  {!rule.source_butler && !rule.source_episode_id && (
                    <span className="text-muted-foreground">
                      No provenance data.
                    </span>
                  )}
                </div>
              </div>

              {/* Tags */}
              {rule.tags.length > 0 && (
                <div>
                  <p className="text-muted-foreground mb-1 text-xs font-medium">
                    Tags
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {rule.tags.map((tag) => (
                      <Badge key={tag} variant="secondary">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Metadata */}
              {Object.keys(rule.metadata).length > 0 && (
                <div>
                  <p className="text-muted-foreground mb-1 text-xs font-medium">
                    Metadata
                  </p>
                  <pre className="rounded-md bg-muted p-3 text-xs overflow-x-auto">
                    {JSON.stringify(rule.metadata, null, 2)}
                  </pre>
                </div>
              )}

              {/* Timestamps */}
              <div className="flex flex-wrap gap-6 text-xs text-muted-foreground">
                <span>
                  Created: <Time value={rule.created_at} mode="absolute" />
                </span>
                {rule.last_applied_at && (
                  <span>
                    Last applied:{" "}
                    <Time value={rule.last_applied_at} mode="absolute" />
                  </span>
                )}
                {rule.last_evaluated_at && (
                  <span>
                    Last evaluated:{" "}
                    <Time value={rule.last_evaluated_at} mode="absolute" />
                  </span>
                )}
              </div>
            </CardContent>
          </Card>
        ) : null
      }
      supporting={null}
      auxiliary={null}
      practical={null}
    />
  );
}
