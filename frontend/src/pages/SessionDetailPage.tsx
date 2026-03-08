import { Link, useParams, useSearchParams } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useSessionDetail } from "@/hooks/use-sessions";
import { useQuery } from "@tanstack/react-query";
import { getSession } from "@/api/index.ts";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { CollapsibleJson, ToolCallTimeline } from "@/components/sessions/ToolCallTimeline";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDuration(ms: number | null): string {
  if (ms == null) return "--";
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  return `${minutes}m ${remainingSeconds}s`;
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return "--";
  return new Date(iso).toLocaleString();
}

function formatTokens(n: number | null): string {
  if (n == null) return "--";
  return n.toLocaleString();
}

function statusBadge(success: boolean | null) {
  if (success === true) {
    return (
      <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">
        Success
      </Badge>
    );
  }
  if (success === false) {
    return <Badge variant="destructive">Failed</Badge>;
  }
  return (
    <Badge variant="outline" className="border-amber-500 text-amber-600">
      Running
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function DetailSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-8 w-64" />
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-32" />
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-24" />
        </CardHeader>
        <CardContent>
          <Skeleton className="h-24 w-full" />
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SessionDetailPage
// ---------------------------------------------------------------------------

export default function SessionDetailPage() {
  const { id = "" } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  const butler = searchParams.get("butler") ?? "";

  // Use butler-scoped endpoint when a butler name is in the query param
  const butlerQuery = useSessionDetail(butler, id);
  const globalQuery = useQuery({
    queryKey: ["session-detail-global", id],
    queryFn: () => getSession(id),
    enabled: !butler && !!id,
  });
  const { data: response, isLoading, isError } = butler ? butlerQuery : globalQuery;
  const session = response?.data;

  if (!id) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold tracking-tight">Session Detail</h1>
        <Card>
          <CardContent>
            <p className="text-muted-foreground py-12 text-center text-sm">
              No session ID provided.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (isLoading) {
    return <DetailSkeleton />;
  }

  if (isError || !session) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Button variant="outline" size="sm" asChild>
            <Link to="/sessions">Back to sessions</Link>
          </Button>
          <h1 className="text-2xl font-bold tracking-tight">Session Detail</h1>
        </div>
        <Card>
          <CardContent>
            <p className="text-destructive py-12 text-center text-sm">
              Failed to load session details. The session may not exist or the butler
              name may be required.
              {!butler && (
                <span className="text-muted-foreground block mt-2">
                  Try adding <code>?butler=name</code> to the URL.
                </span>
              )}
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <Breadcrumbs items={[{ label: "Sessions", href: "/sessions" }, { label: id.slice(0, 8) }]} />
      <div className="flex items-center gap-4">
        <h1 className="text-2xl font-bold tracking-tight">Session Detail</h1>
        {statusBadge(session.success)}
      </div>

      {/* Session metadata */}
      <Card>
        <CardHeader>
          <CardTitle>Metadata</CardTitle>
          <CardDescription>
            <code className="text-xs">{session.id}</code>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 text-sm">
            {butler && (
              <>
                <dt className="text-muted-foreground font-medium">Butler</dt>
                <dd>
                  <Link
                    to={`/butlers/${encodeURIComponent(butler)}`}
                    className="text-primary underline-offset-4 hover:underline"
                  >
                    {butler}
                  </Link>
                </dd>
              </>
            )}

            <dt className="text-muted-foreground font-medium">Trigger Source</dt>
            <dd><Badge variant="secondary">{session.trigger_source}</Badge></dd>

            <dt className="text-muted-foreground font-medium">Started</dt>
            <dd>{formatTimestamp(session.started_at)}</dd>

            <dt className="text-muted-foreground font-medium">Completed</dt>
            <dd>{formatTimestamp(session.completed_at)}</dd>

            <dt className="text-muted-foreground font-medium">Duration</dt>
            <dd>{formatDuration(session.duration_ms)}</dd>

            {session.model && (
              <>
                <dt className="text-muted-foreground font-medium">Model</dt>
                <dd>{session.model}</dd>
              </>
            )}

            {(session.input_tokens != null || session.output_tokens != null) && (
              <>
                <dt className="text-muted-foreground font-medium">Tokens (in/out)</dt>
                <dd>
                  {formatTokens(session.input_tokens)} / {formatTokens(session.output_tokens)}
                </dd>
              </>
            )}
          </dl>
        </CardContent>
      </Card>

      {/* Tool calls */}
      <Card>
        <CardHeader>
          <CardTitle>
            Tool Calls ({Array.isArray(session.tool_calls) ? session.tool_calls.length : 0})
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ToolCallTimeline
            toolCalls={Array.isArray(session.tool_calls) ? session.tool_calls : []}
            resultText={session.result}
          />
        </CardContent>
      </Card>

      {/* Prompt */}
      <Card>
        <CardHeader>
          <CardTitle>Prompt</CardTitle>
        </CardHeader>
        <CardContent>
          <pre className="overflow-auto rounded-md bg-muted p-4 text-sm font-mono whitespace-pre-wrap">
            {session.prompt}
          </pre>
        </CardContent>
      </Card>

      {/* Result */}
      {session.result && (
        <Card>
          <CardHeader>
            <CardTitle>Result</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="overflow-auto rounded-md bg-muted p-4 text-sm font-mono whitespace-pre-wrap">
              {session.result}
            </pre>
          </CardContent>
        </Card>
      )}

      {/* Error */}
      {session.error && (
        <Card>
          <CardHeader>
            <CardTitle className="text-destructive">Error</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="overflow-auto rounded-md bg-destructive/10 p-4 text-sm font-mono whitespace-pre-wrap text-destructive">
              {session.error}
            </pre>
          </CardContent>
        </Card>
      )}

      {/* Cost */}
      {session.cost != null && Object.keys(session.cost).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Cost</CardTitle>
          </CardHeader>
          <CardContent>
            <CollapsibleJson label="Cost breakdown" data={session.cost} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
