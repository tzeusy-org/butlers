import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Time } from "@/components/ui/time";
import { useHeldCaptures } from "@/hooks/use-captures";

/**
 * Held-capture lane (bu-2jtfw.9): captures whose routing session never
 * finished. A held capture renders as held, never as saved -- it is a
 * ledger row, not a promise that something was filed away. The owner can
 * see it here and act (Dispatch's held-capture verbs land in a later
 * slice; this panel is the read surface that proves the ledger is real).
 */
export default function HeldCapturesPanel() {
  const { data, isError, isLoading, refetch } = useHeldCaptures();

  if (isError || (data && data.meta.sources_degraded && data.meta.sources_degraded.length > 0)) {
    return (
      <Card data-testid="held-captures-panel">
        <CardHeader>
          <CardTitle>Held captures</CardTitle>
        </CardHeader>
        <CardContent>
          <SourceDegradedNote
            label="Captures ledger"
            detail="unavailable, so held captures cannot be shown"
            onRetry={() => void refetch()}
            testId="held-captures-unavailable"
          />
        </CardContent>
      </Card>
    );
  }

  if (isLoading || !data) return null;

  const captures = data.data;

  return (
    <Card data-testid="held-captures-panel">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Held captures</CardTitle>
        <Badge variant="outline">{captures.length}</Badge>
      </CardHeader>
      <CardContent>
        {captures.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="held-captures-empty">
            Nothing held -- every capture has been routed.
          </p>
        ) : (
          <ul className="space-y-3" data-testid="held-captures-list">
            {captures.map((capture) => (
              <li
                key={capture.capture_id}
                className="flex items-start justify-between gap-3"
                data-testid="held-capture-row"
              >
                <div className="space-y-1">
                  <p className="text-sm">{capture.content}</p>
                  <p className="text-xs text-muted-foreground">
                    via {capture.channel} · <Time value={capture.created_at} mode="relative" />
                  </p>
                </div>
                <Badge variant="outline" data-testid="held-capture-badge">
                  Held
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
