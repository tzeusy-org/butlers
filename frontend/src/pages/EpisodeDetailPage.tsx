import { useParams } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useEpisode } from "@/hooks/use-memory";

export default function EpisodeDetailPage() {
  const { episodeId } = useParams<{ episodeId: string }>();
  const { data, isLoading, error } = useEpisode(episodeId);
  const episode = data?.data;

  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[
          { label: "Memory", href: "/memory" },
          { label: "Episodes", href: "/memory?tab=episodes" },
          { label: "Episode" },
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
          Failed to load episode. {(error as Error).message}
        </div>
      )}

      {episode && (
        <>
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center gap-3">
                <CardTitle className="text-2xl">Episode</CardTitle>
                <Badge variant="outline">{episode.butler}</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Content */}
              <div>
                <p className="text-muted-foreground mb-1 text-sm font-medium">
                  Content
                </p>
                <div className="rounded-md bg-muted/30 p-4">
                  <p className="text-sm whitespace-pre-wrap break-words">
                    {episode.content}
                  </p>
                </div>
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
                      {new Date(episode.expires_at).toLocaleString()}
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

              {/* Timestamps */}
              <div className="flex flex-wrap gap-6 text-xs text-muted-foreground">
                <span>
                  Created: {new Date(episode.created_at).toLocaleString()}
                </span>
                {episode.last_referenced_at && (
                  <span>
                    Last referenced:{" "}
                    {new Date(episode.last_referenced_at).toLocaleString()}
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
