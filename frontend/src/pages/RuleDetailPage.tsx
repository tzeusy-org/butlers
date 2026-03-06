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

function progressBar(value: number, label: string) {
  const pct = Math.round(value * 100);
  return (
    <div>
      <p className="text-muted-foreground mb-1 text-xs font-medium">{label}</p>
      <div className="flex items-center gap-2">
        <div className="bg-muted h-2 w-24 overflow-hidden rounded-full">
          <div
            className="bg-primary h-full rounded-full"
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="text-muted-foreground text-sm">{pct}%</span>
      </div>
    </div>
  );
}

export default function RuleDetailPage() {
  const { ruleId } = useParams<{ ruleId: string }>();
  const { data, isLoading, error } = useRule(ruleId ?? null);
  const rule = data?.data;

  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[
          { label: "Memory", href: "/memory" },
          { label: "Rules", href: "/memory?tab=rules" },
          { label: "Rule" },
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
          Failed to load rule. {(error as Error).message}
        </div>
      )}

      {rule && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-2xl">Rule</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
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
                {progressBar(rule.effectiveness_score, "Effectiveness")}
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
                {progressBar(rule.confidence, "Confidence")}
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
                  Created: {new Date(rule.created_at).toLocaleString()}
                </span>
                {rule.last_applied_at && (
                  <span>
                    Last applied:{" "}
                    {new Date(rule.last_applied_at).toLocaleString()}
                  </span>
                )}
                {rule.last_evaluated_at && (
                  <span>
                    Last evaluated:{" "}
                    {new Date(rule.last_evaluated_at).toLocaleString()}
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
