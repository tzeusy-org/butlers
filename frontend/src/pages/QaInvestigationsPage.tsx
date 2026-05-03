import { useState } from "react";
import { Link } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useQaInvestigations } from "@/hooks/use-qa";
import type { QaInvestigation } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 20;

const STATUS_OPTIONS = [
  { value: "all", label: "All statuses" },
  { value: "dispatch_pending", label: "Queued" },
  { value: "investigating", label: "Investigating" },
  { value: "pr_open", label: "PR Open" },
  { value: "pr_merged", label: "PR Merged" },
  { value: "failed", label: "Failed" },
  { value: "timeout", label: "Timeout" },
  { value: "unfixable", label: "Unfixable" },
  { value: "anonymization_failed", label: "Anon Failed" },
] as const;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// NOTE: formatRelative uses a custom compact format ("just now", "5m ago", "2h ago", "3d ago")
// that differs from date-fns formatDistanceToNow. Not migrated to <Time> — kept intentionally.
function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "--";
  const diff = Date.now() - new Date(iso).getTime();
  const minutes = Math.round(diff / 60_000);
  if (minutes < 2) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

// ---------------------------------------------------------------------------
// Badges (same as overview page)
// ---------------------------------------------------------------------------

function SeverityBadge({ severity }: { severity: number }) {
  const labels: Record<number, string> = { 0: "critical", 1: "high", 2: "medium", 3: "low", 4: "info" };
  const label = labels[severity] ?? String(severity);
  const classNames: Record<number, string> = {
    0: "bg-red-600 text-white hover:bg-red-600/90",
    1: "bg-orange-500 text-white hover:bg-orange-500/90",
    2: "bg-yellow-500 text-white hover:bg-yellow-500/90",
    3: "bg-slate-400 text-white hover:bg-slate-400/90",
    4: "bg-sky-400 text-white hover:bg-sky-400/90",
  };
  return <Badge className={classNames[severity] ?? ""}>{label}</Badge>;
}

function StatusBadge({ status }: { status: string }) {
  const config: Record<string, { label: string; className: string }> = {
    investigating: { label: "investigating", className: "border-amber-500 text-amber-600" },
    dispatch_pending: { label: "pending", className: "border-blue-500 text-blue-600" },
    pr_open: { label: "PR open", className: "border-blue-500 text-blue-600" },
    pr_merged: { label: "PR merged", className: "bg-emerald-600 text-white hover:bg-emerald-600/90" },
    failed: { label: "failed", className: "" },
    timeout: { label: "timeout", className: "" },
    unfixable: { label: "unfixable", className: "" },
    anonymization_failed: { label: "anon failed", className: "" },
  };

  const c = config[status];
  if (!c) return <Badge variant="outline">{status}</Badge>;
  if (status === "pr_merged") return <Badge className={c.className}>{c.label}</Badge>;
  if (["failed", "timeout", "unfixable", "anonymization_failed"].includes(status)) {
    return <Badge variant="destructive">{c.label}</Badge>;
  }
  return (
    <Badge variant="outline" className={c.className}>
      {c.label}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Table row
// ---------------------------------------------------------------------------

function InvestigationRow({ inv }: { inv: QaInvestigation }) {
  return (
    <TableRow className="cursor-pointer hover:bg-muted/50">
      <TableCell>
        <Link
          to={`/qa/investigations/${inv.id}`}
          className="text-primary underline-offset-4 hover:underline font-mono text-xs"
        >
          {inv.id.slice(0, 8)}
        </Link>
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-1.5">
          <StatusBadge status={inv.status} />
        </div>
      </TableCell>
      <TableCell>
        <SeverityBadge severity={inv.severity} />
      </TableCell>
      <TableCell>
        <Badge variant="outline" className="font-mono text-xs">
          {inv.butler_name}
        </Badge>
      </TableCell>
      <TableCell>
        <code className="text-xs">{inv.exception_type}</code>
      </TableCell>
      <TableCell>
        {inv.pr_number ? (
          inv.pr_url ? (
            <a
              href={inv.pr_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary text-xs underline-offset-4 hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              #{inv.pr_number}
            </a>
          ) : (
            <span className="text-xs">#{inv.pr_number}</span>
          )
        ) : (
          <span className="text-muted-foreground text-xs">--</span>
        )}
      </TableCell>
      <TableCell>
        {/* Uses compact relative format intentionally; <Time> would use date-fns natural language */}
        <span className="text-xs text-muted-foreground">
          {formatRelative(inv.created_at)}
        </span>
      </TableCell>
    </TableRow>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function TableSkeleton() {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {["ID", "Status", "Severity", "Butler", "Exception", "PR", "Created"].map((h) => (
            <TableHead key={h}>{h}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {Array.from({ length: 5 }).map((_, i) => (
          <TableRow key={i}>
            {Array.from({ length: 7 }).map((_, j) => (
              <TableCell key={j}>
                <Skeleton className="h-4 w-full" />
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------------------
// QaInvestigationsPage
// ---------------------------------------------------------------------------

export default function QaInvestigationsPage() {
  const [statusFilter, setStatusFilter] = useState("all");
  const [page, setPage] = useState(0);

  const params = {
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    ...(statusFilter !== "all" ? { status: statusFilter } : {}),
  };

  const { data: response, isLoading, isError } = useQaInvestigations(params);
  const investigations = response?.data ?? [];
  const meta = response?.meta;
  const total = meta?.total ?? 0;
  const hasMore = meta?.has_more ?? false;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = page + 1;

  function handleStatusChange(value: string) {
    setStatusFilter(value);
    setPage(0);
  }

  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[
          { label: "QA", href: "/qa" },
          { label: "Investigations" },
        ]}
      />

      <div>
        <h1 className="text-3xl font-bold tracking-tight">Investigations</h1>
        <p className="text-muted-foreground mt-1">
          All QA investigation attempts across butlers.
        </p>
      </div>

      {/* Filter bar */}
      <Card>
        <CardContent className="pt-0">
          <div className="flex flex-wrap items-end gap-4">
            <div className="space-y-1">
              <label className="text-muted-foreground text-xs font-medium">Status</label>
              <Select value={statusFilter} onValueChange={handleStatusChange}>
                <SelectTrigger className="w-48">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STATUS_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {statusFilter !== "all" && (
              <Button variant="ghost" size="sm" onClick={() => handleStatusChange("all")}>
                Clear filter
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Table */}
      <Card>
        <CardContent>
          {isLoading ? (
            <TableSkeleton />
          ) : isError ? (
            <p className="text-sm text-destructive text-center py-8">
              Failed to load investigations.
            </p>
          ) : investigations.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">
              No investigations found.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>ID</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Severity</TableHead>
                  <TableHead>Butler</TableHead>
                  <TableHead>Exception</TableHead>
                  <TableHead>PR</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {investigations.map((inv) => (
                  <InvestigationRow key={inv.id} inv={inv} />
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Pagination */}
      {total > 0 && (
        <div className="flex items-center justify-between">
          <p className="text-muted-foreground text-sm">
            Page {currentPage} of {totalPages} ({total} total)
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
