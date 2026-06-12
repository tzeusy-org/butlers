import { useMemo } from "react";
import { useParams } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Time } from "@/components/ui/time";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DetailPage } from "@/components/layout/DetailPage";
import { useEpisode } from "@/hooks/use-memory";

export default function EpisodeDetailPage() {
  const { episodeId } = useParams<{ episodeId: string }>();
  const { data, isLoading, error } = useEpisode(episodeId);
  const episode = data?.data;

  // Derive record fields from the loaded episode (or a loading placeholder).
  // title  = first non-empty line of content (trimmed), capped at 80 chars;
  //          leading blank lines are skipped so whitespace-padded content
  //          does not produce a blank title.
  // subtitle = source butler (the "lane")
  const title = useMemo(() => {
    if (!episode) return "Episode";
    const firstLine = episode.content.split("\n").map((l) => l.trim()).find((l) => l.length > 0) ?? "";
    return firstLine.length > 80 ? firstLine.slice(0, 77) + "…" : firstLine || "Episode";
  }, [episode]);

  const subtitle = episode?.butler ?? undefined;

  const breadcrumbs = useMemo(
    () => [
      { label: "Memory", href: "/memory" },
      { label: "Episodes", href: "/memory?register=episodes" },
      { label: title },
    ],
    [title],
  );

  return (
    <DetailPage
      record={{ title, subtitle, type: "episode" }}
      breadcrumbs={breadcrumbs}
      loading={isLoading}
      error={error ?? null}
      pulse={null}
      primary={
        episode ? (
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center gap-3">
                <CardTitle>Content</CardTitle>
                <Badge variant="outline">{episode.butler}</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Full content */}
              <div className="rounded-md bg-muted/30 p-4">
                <p className="text-sm whitespace-pre-wrap break-words">
                  {episode.content}
                </p>
              </div>

              {/* Status row */}
              <div className="flex flex-wrap items-center gap-4">
                <div>
                  <p className="text-muted-foreground mb-1 text-xs font-medium">
                    Importance
                  </p>
                  <span className="text-sm tabular-nums">
                    {episode.importance.toFixed(1)}
                  </span>
                </div>
                <div>
                  <p className="text-muted-foreground mb-1 text-xs font-medium">
                    Consolidated
                  </p>
                  {episode.consolidated ? (
                    <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">
                      Yes
                    </Badge>
                  ) : (
                    <Badge variant="secondary">No</Badge>
                  )}
                </div>
              </div>

              {/* Details */}
              <div className="flex flex-wrap gap-6 text-sm">
                {episode.session_id && (
                  <div>
                    <span className="text-muted-foreground">Session ID: </span>
                    <span className="font-mono text-xs">
                      {episode.session_id}
                    </span>
                  </div>
                )}
                <div>
                  <span className="text-muted-foreground">
                    Reference count:{" "}
                  </span>
                  <span className="tabular-nums">
                    {episode.reference_count}
                  </span>
                </div>
                {episode.expires_at && (
                  <div>
                    <span className="text-muted-foreground">Expires: </span>
                    <span>
                      <Time value={episode.expires_at} mode="absolute" />
                    </span>
                  </div>
                )}
              </div>

              {/* Metadata */}
              {Object.keys(episode.metadata).length > 0 && (
                <div>
                  <p className="text-muted-foreground mb-1 text-xs font-medium">
                    Metadata
                  </p>
                  <pre className="rounded-md bg-muted p-3 text-xs overflow-x-auto">
                    {JSON.stringify(episode.metadata, null, 2)}
                  </pre>
                </div>
              )}
            </CardContent>
          </Card>
        ) : null
      }
      supporting={
        episode ? (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Provenance</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-6 text-xs text-muted-foreground">
                <span>
                  Created: <Time value={episode.created_at} mode="absolute" />
                </span>
                {episode.last_referenced_at && (
                  <span>
                    Last referenced:{" "}
                    <Time value={episode.last_referenced_at} mode="absolute" />
                  </span>
                )}
              </div>
            </CardContent>
          </Card>
        ) : null
      }
    />
  );
}
