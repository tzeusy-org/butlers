import { Fragment, useState } from "react";

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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useEpisodes, useFacts, useRules } from "@/hooks/use-memory";
import type { EpisodeParams, FactParams, RuleParams } from "@/api/types";

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

function permanenceBadge(p: string) {
  const colors: Record<string, string> = {
    permanent: "bg-blue-600 text-white hover:bg-blue-600/90",
    stable: "bg-sky-600 text-white hover:bg-sky-600/90",
    standard: "",
    volatile: "border-amber-500 text-amber-600",
    ephemeral: "border-red-500 text-red-500",
  };
  const cls = colors[p];
  if (cls === undefined) return <Badge variant="secondary">{p}</Badge>;
  if (cls === "") return <Badge variant="secondary">{p}</Badge>;
  if (cls.startsWith("border-"))
    return (
      <Badge variant="outline" className={cls}>
        {p}
      </Badge>
    );
  return <Badge className={cls}>{p}</Badge>;
}

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

function truncate(s: string, len = 80) {
  return s.length > len ? s.slice(0, len) + "..." : s;
}

function confidenceBar(value: number) {
  const pct = Math.round(value * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="bg-muted h-2 w-16 overflow-hidden rounded-full">
        <div
          className="bg-primary h-full rounded-full"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-muted-foreground text-xs">{pct}%</span>
    </div>
  );
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
// Facts Tab
// ---------------------------------------------------------------------------

function FactsTab({ butlerScope }: { butlerScope?: string }) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);

  const params: FactParams = {
    q: search || undefined,
    scope: butlerScope,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data: response, isLoading } = useFacts(params);
  const facts = response?.data ?? [];
  const total = response?.meta?.total ?? 0;

  return (
    <div className="space-y-4">
      <Input
        placeholder="Search facts..."
        value={search}
        onChange={(e) => {
          setSearch(e.target.value);
          setPage(0);
        }}
      />

      {isLoading ? (
        <TableSkeleton cols={6} />
      ) : facts.length === 0 ? (
        <p className="text-muted-foreground py-8 text-center text-sm">
          No facts found.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Subject</TableHead>
                <TableHead>Predicate</TableHead>
                <TableHead>Content</TableHead>
                <TableHead>Confidence</TableHead>
                <TableHead>Permanence</TableHead>
                <TableHead>Validity</TableHead>
                <TableHead>Scope</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {facts.map((f) => (
                <TableRow key={f.id}>
                  <TableCell className="font-medium">{f.subject}</TableCell>
                  <TableCell>{f.predicate}</TableCell>
                  <TableCell className="max-w-xs truncate">
                    {truncate(f.content)}
                  </TableCell>
                  <TableCell>{confidenceBar(f.confidence)}</TableCell>
                  <TableCell>{permanenceBadge(f.permanence)}</TableCell>
                  <TableCell>{validityBadge(f.validity)}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{f.scope}</Badge>
                  </TableCell>
                </TableRow>
              ))}
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
// Rules Tab
// ---------------------------------------------------------------------------

function RulesTab({ butlerScope }: { butlerScope?: string }) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);

  const params: RuleParams = {
    q: search || undefined,
    scope: butlerScope,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data: response, isLoading } = useRules(params);
  const rules = response?.data ?? [];
  const total = response?.meta?.total ?? 0;

  return (
    <div className="space-y-4">
      <Input
        placeholder="Search rules..."
        value={search}
        onChange={(e) => {
          setSearch(e.target.value);
          setPage(0);
        }}
      />

      {isLoading ? (
        <TableSkeleton cols={5} />
      ) : rules.length === 0 ? (
        <p className="text-muted-foreground py-8 text-center text-sm">
          No rules found.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Content</TableHead>
                <TableHead>Maturity</TableHead>
                <TableHead>Effectiveness</TableHead>
                <TableHead>Applied</TableHead>
                <TableHead>Scope</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rules.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="max-w-sm truncate">
                    {truncate(r.content)}
                  </TableCell>
                  <TableCell>{maturityBadge(r.maturity)}</TableCell>
                  <TableCell>
                    {Math.round(r.effectiveness_score * 100)}%
                  </TableCell>
                  <TableCell>{r.applied_count}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{r.scope}</Badge>
                  </TableCell>
                </TableRow>
              ))}
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
// Episodes Tab
// ---------------------------------------------------------------------------

function EpisodesTab({ butlerScope }: { butlerScope?: string }) {
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
                    <TableRow>
                      <TableCell className="max-w-sm align-top">
                        <div className="space-y-1">
                          <p className="truncate">{truncate(ep.content)}</p>
                          <Button
                            type="button"
                            variant="link"
                            size="xs"
                            className="h-auto px-0 text-xs"
                            onClick={() =>
                              setExpandedEpisodeId((prev) =>
                                prev === ep.id ? null : ep.id,
                              )
                            }
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
                        {new Date(ep.created_at).toLocaleString()}
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
            <FactsTab butlerScope={butlerScope} />
          </TabsContent>

          <TabsContent value="rules">
            <RulesTab butlerScope={butlerScope} />
          </TabsContent>

          <TabsContent value="episodes">
            <EpisodesTab butlerScope={butlerScope} />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
