import { Fragment, useState } from "react";
import { useNavigate } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Time } from "@/components/ui/time";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import FactsRegister from "@/components/memory/FactsRegister";
import RulesRegister from "@/components/memory/RulesRegister";
import { useEpisodes } from "@/hooks/use-memory";
import type { EpisodeParams } from "@/api/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface MemoryBrowserProps {
  /** When set, filter all queries to this butler scope. */
  butlerScope?: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 20;

// ---------------------------------------------------------------------------
// Badge helpers
// ---------------------------------------------------------------------------

function truncate(s: string, len = 80) {
  return s.length > len ? s.slice(0, len) + "..." : s;
}

// ---------------------------------------------------------------------------
// Pagination helper
// ---------------------------------------------------------------------------

function PaginationControls({
  page,
  total,
  pageSize,
  onPageChange,
}: {
  page: number;
  total: number;
  pageSize: number;
  onPageChange: (p: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const hasMore = (page + 1) * pageSize < total;

  if (total === 0) return null;

  return (
    <div className="flex items-center justify-between pt-4">
      <p className="text-muted-foreground text-sm">
        Page {page + 1} of {totalPages}
      </p>
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={page === 0}
          onClick={() => onPageChange(Math.max(0, page - 1))}
        >
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!hasMore}
          onClick={() => onPageChange(page + 1)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Table skeleton
// ---------------------------------------------------------------------------

function TableSkeleton({ cols, rows = 5 }: { cols: number; rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex gap-4">
          {Array.from({ length: cols }, (_, j) => (
            <Skeleton key={j} className="h-6 flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Episodes Tab
// ---------------------------------------------------------------------------

function EpisodesTab({ butlerScope }: { butlerScope?: string }) {
  const navigate = useNavigate();
  const [page, setPage] = useState(0);
  const [expandedEpisodeId, setExpandedEpisodeId] = useState<string | null>(
    null,
  );

  const params: EpisodeParams = {
    butler: butlerScope,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data: response, isLoading } = useEpisodes(params);
  const episodes = response?.data ?? [];
  const total = response?.meta?.total ?? 0;

  return (
    <div className="space-y-4">
      {isLoading ? (
        <TableSkeleton cols={5} />
      ) : episodes.length === 0 ? (
        <p className="text-muted-foreground py-8 text-center text-sm">
          No episodes found.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Content</TableHead>
                <TableHead>Butler</TableHead>
                <TableHead>Importance</TableHead>
                <TableHead>Consolidated</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {episodes.map((ep) => {
                const isExpanded = expandedEpisodeId === ep.id;

                return (
                  <Fragment key={ep.id}>
                    <TableRow
                      className="cursor-pointer"
                      onClick={() => navigate(`/memory/episodes/${ep.id}`)}
                    >
                      <TableCell className="max-w-sm align-top">
                        <div className="space-y-1">
                          <p className="truncate">{truncate(ep.content)}</p>
                          <Button
                            type="button"
                            variant="link"
                            size="xs"
                            className="h-auto px-0 text-xs"
                            onClick={(e) => {
                              e.stopPropagation();
                              setExpandedEpisodeId((prev) =>
                                prev === ep.id ? null : ep.id,
                              );
                            }}
                          >
                            {isExpanded ? "Collapse" : "Expand"}
                          </Button>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{ep.butler}</Badge>
                      </TableCell>
                      <TableCell>{ep.importance.toFixed(1)}</TableCell>
                      <TableCell>
                        {ep.consolidated ? (
                          <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">
                            Yes
                          </Badge>
                        ) : (
                          <Badge variant="secondary">No</Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        <Time value={ep.created_at} mode="absolute" />
                      </TableCell>
                    </TableRow>

                    {isExpanded && (
                      <TableRow>
                        <TableCell colSpan={5} className="bg-muted/30 p-4">
                          <div className="space-y-2">
                            <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
                              Episode Content
                            </p>
                            <p className="text-sm whitespace-pre-wrap break-words">
                              {ep.content}
                            </p>
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      <PaginationControls
        page={page}
        total={total}
        pageSize={PAGE_SIZE}
        onPageChange={setPage}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// MemoryBrowser
// ---------------------------------------------------------------------------

export default function MemoryBrowser({ butlerScope }: MemoryBrowserProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Memory Browser</CardTitle>
        <CardDescription>
          Browse facts, rules, and episodes across the memory subsystem
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="facts">
          <TabsList>
            <TabsTrigger value="facts">Facts</TabsTrigger>
            <TabsTrigger value="rules">Rules</TabsTrigger>
            <TabsTrigger value="episodes">Episodes</TabsTrigger>
          </TabsList>

          <TabsContent value="facts">
            <FactsRegister butlerScope={butlerScope} />
          </TabsContent>

          <TabsContent value="rules">
            <RulesRegister butlerScope={butlerScope} />
          </TabsContent>

          <TabsContent value="episodes">
            <EpisodesTab butlerScope={butlerScope} />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
