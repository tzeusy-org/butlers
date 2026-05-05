import { useState } from "react";
import { Time } from "@/components/ui/time";

import type { ResearchParams } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useResearch } from "@/hooks/use-health";
import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-48" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

function EmptyState() {
  return (
    <EmptyStateUI
      title="No research found."
      description="Research notes appear as the Health butler tracks articles and references."
    />
  );
}

// ---------------------------------------------------------------------------
// ResearchPage
// ---------------------------------------------------------------------------

export default function ResearchPage() {
  const [search, setSearch] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [page, setPage] = useState(0);

  const params: ResearchParams = {
    q: search || undefined,
    tag: tagFilter || undefined,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data, isLoading } = useResearch(params);

  const notes = data?.data ?? [];
  const total = data?.meta?.total ?? 0;
  const hasMore = data?.meta?.has_more ?? false;

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  // Collect unique tags for quick filter
  const allTags = Array.from(new Set(notes.flatMap((n) => n.tags)));

  function handleSearchChange(value: string) {
    setSearch(value);
    setPage(0);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Research</h1>
        <p className="text-muted-foreground mt-1">
          Health research notes, articles, and references.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Research Notes</CardTitle>
          <CardDescription>
            {total > 0
              ? `${total.toLocaleString()} note${total !== 1 ? "s" : ""}`
              : ""}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Filters */}
          <div className="flex flex-wrap items-center gap-3">
            <Input
              placeholder="Search research..."
              value={search}
              onChange={(e) => handleSearchChange(e.target.value)}
              className="w-64"
            />
            {allTags.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge
                  variant={tagFilter === "" ? "default" : "outline"}
                  className="cursor-pointer"
                  onClick={() => {
                    setTagFilter("");
                    setPage(0);
                  }}
                >
                  All tags
                </Badge>
                {allTags.map((tag) => (
                  <Badge
                    key={tag}
                    variant={tagFilter === tag ? "default" : "outline"}
                    className="cursor-pointer"
                    onClick={() => {
                      setTagFilter(tagFilter === tag ? "" : tag);
                      setPage(0);
                    }}
                  >
                    {tag}
                  </Badge>
                ))}
              </div>
            )}
            {(search || tagFilter) && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearch("");
                  setTagFilter("");
                  setPage(0);
                }}
              >
                Clear
              </Button>
            )}
          </div>

          {/* Table */}
          {!isLoading && notes.length === 0 ? (
            <EmptyState />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Title</TableHead>
                  <TableHead>Tags</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Updated</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  <SkeletonRows />
                ) : (
                  notes.map((note) => (
                    <>
                      <TableRow
                        key={note.id}
                        className="cursor-pointer"
                        onClick={() =>
                          setExpandedId(expandedId === note.id ? null : note.id)
                        }
                      >
                        <TableCell className="font-medium">{note.title}</TableCell>
                        <TableCell>
                          <div className="flex flex-wrap gap-1">
                            {note.tags.map((tag) => (
                              <Badge key={tag} variant="outline" className="text-xs">
                                {tag}
                              </Badge>
                            ))}
                            {note.tags.length === 0 && (
                              <span className="text-muted-foreground text-xs">{"\u2014"}</span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="text-sm">
                          {note.source_url ? (
                            <a
                              href={note.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-blue-600 hover:underline dark:text-blue-400"
                              onClick={(e) => e.stopPropagation()}
                            >
                              Link
                            </a>
                          ) : (
                            <span className="text-muted-foreground text-xs">{"\u2014"}</span>
                          )}
                        </TableCell>
                        <TableCell className="text-muted-foreground text-sm">
                          <Time value={note.created_at} mode="absolute" precision="day" />
                        </TableCell>
                      </TableRow>
                      {expandedId === note.id && (
                        <TableRow key={`${note.id}-content`}>
                          <TableCell colSpan={4}>
                            <div className="prose dark:prose-invert max-w-none py-3 text-sm whitespace-pre-wrap">
                              {note.content}
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </>
                  ))
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Pagination */}
      {total > 0 && (
        <div className="flex items-center justify-between">
          <p className="text-muted-foreground text-sm">
            Showing {rangeStart}–{rangeEnd} of {total.toLocaleString()}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!hasMore}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
