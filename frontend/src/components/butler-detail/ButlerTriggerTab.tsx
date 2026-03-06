import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { triggerButler } from "@/api/index.ts";
import { uuidv7 } from "@/lib/ids.ts";
import { ApiError } from "@/api/client.ts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ButlerTriggerTabProps {
  butlerName: string;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TriggerHistoryEntry {
  id: string;
  prompt: string;
  success: boolean;
  sessionId: string | null;
  output: string;
  timestamp: string;
}

// ---------------------------------------------------------------------------
// ButlerTriggerTab
// ---------------------------------------------------------------------------

export default function ButlerTriggerTab({ butlerName }: ButlerTriggerTabProps) {
  const [searchParams] = useSearchParams();
  const skillParam = searchParams.get("skill");

  const [prompt, setPrompt] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [lastResult, setLastResult] = useState<{
    success: boolean;
    sessionId: string | null;
    output: string;
    error?: string;
  } | null>(null);
  const [history, setHistory] = useState<TriggerHistoryEntry[]>([]);

  // Pre-fill prompt when skill param is present
  useEffect(() => {
    if (skillParam) {
      setPrompt(`Use the ${skillParam} skill to `);
    }
  }, [skillParam]);

  const handleSubmit = useCallback(async () => {
    if (!prompt.trim() || isSubmitting) return;

    setIsSubmitting(true);
    setLastResult(null);

    try {
      const response = await triggerButler(butlerName, prompt.trim());

      const result = {
        success: response.success,
        sessionId: response.session_id,
        output: response.output,
      };

      setLastResult(result);

      // Add to history
      setHistory((prev) => [
        {
          id: uuidv7(),
          prompt: prompt.trim(),
          success: response.success,
          sessionId: response.session_id,
          output: response.output,
          timestamp: new Date().toISOString(),
        },
        ...prev,
      ]);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "An unexpected error occurred";

      setLastResult({
        success: false,
        sessionId: null,
        output: "",
        error: message,
      });

      setHistory((prev) => [
        {
          id: uuidv7(),
          prompt: prompt.trim(),
          success: false,
          sessionId: null,
          output: message,
          timestamp: new Date().toISOString(),
        },
        ...prev,
      ]);
    } finally {
      setIsSubmitting(false);
    }
  }, [butlerName, prompt, isSubmitting]);

  return (
    <div className="space-y-6">
      {/* Prompt Input */}
      <Card>
        <CardHeader>
          <CardTitle>Trigger Session</CardTitle>
          <CardDescription>
            Send a prompt to trigger a Claude Code session for this butler
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Enter a prompt to trigger a CC session..."
            className="min-h-32"
            disabled={isSubmitting}
          />
          <Button onClick={handleSubmit} disabled={!prompt.trim() || isSubmitting}>
            {isSubmitting ? "Triggering..." : "Trigger Session"}
          </Button>
        </CardContent>
      </Card>

      {/* Result Display */}
      {lastResult && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-3">
              Result
              {lastResult.success ? (
                <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">
                  Success
                </Badge>
              ) : (
                <Badge variant="destructive">Failed</Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {lastResult.error ? (
              <p className="text-sm text-destructive">{lastResult.error}</p>
            ) : (
              <pre className="overflow-auto rounded-md bg-muted p-4 text-sm font-mono whitespace-pre-wrap">
                {lastResult.output}
              </pre>
            )}
            {lastResult.sessionId && (
              <p className="text-sm text-muted-foreground">
                Session:{" "}
                <Link
                  to={`/sessions/${lastResult.sessionId}`}
                  className="text-primary underline underline-offset-4 hover:text-primary/80"
                >
                  {lastResult.sessionId}
                </Link>
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* History */}
      {history.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Trigger History</CardTitle>
            <CardDescription>
              Previous triggers from this page session (not persisted)
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {history.map((entry) => (
                <div
                  key={entry.id}
                  className="flex items-start gap-3 rounded-md border p-3 text-sm"
                >
                  <div className="shrink-0 pt-0.5">
                    {entry.success ? (
                      <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">
                        OK
                      </Badge>
                    ) : (
                      <Badge variant="destructive">Fail</Badge>
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">{entry.prompt}</p>
                    <p className="text-muted-foreground text-xs">
                      {new Date(entry.timestamp).toLocaleTimeString()}
                      {entry.sessionId && (
                        <>
                          {" \u2014 "}
                          <Link
                            to={`/sessions/${entry.sessionId}`}
                            className="text-primary underline underline-offset-4 hover:text-primary/80"
                          >
                            {entry.sessionId.slice(0, 8)}...
                          </Link>
                        </>
                      )}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
