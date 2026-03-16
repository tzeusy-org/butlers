import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import type { ApprovalAction, ApprovalActionParams } from "@/api/types";
import { ActionTable } from "@/components/approvals/action-table";
import { ActionDetailDialog } from "@/components/approvals/action-detail-dialog";
import { ApprovalMetricsBar } from "@/components/approvals/approval-metrics";
import { HistoryTable } from "@/components/approvals/history-table";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useApprovalAction,
  useApprovalActions,
  useApprovalMetrics,
  useExpireStaleActions,
} from "@/hooks/use-approvals";
import { useAutoRefresh } from "@/hooks/use-auto-refresh";

const PAGE_SIZE = 20;
const HISTORY_LIMIT = 10;
const RESOLVED_STATUSES = new Set(["approved", "rejected", "expired", "executed"]);

const STATUS_OPTIONS = [
  { value: "all", label: "All statuses" },
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "expired", label: "Expired" },
  { value: "executed", label: "Executed" },
] as const;

interface FilterState {
  tool_name: string;
  status: string;
  butler: string;
}

const EMPTY_FILTERS: FilterState = {
  tool_name: "",
  status: "pending",
  butler: "",
};

export default function ApprovalsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [page, setPage] = useState(0);
  const [selectedAction, setSelectedAction] = useState<ApprovalAction | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  const actionIdParam = searchParams.get("action");

  useAutoRefresh(); // Auto-refresh enabled

  const params: ApprovalActionParams = {
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    ...(filters.tool_name ? { tool_name: filters.tool_name } : {}),
    ...(filters.status !== "all" ? { status: filters.status } : {}),
    ...(filters.butler ? { butler: filters.butler } : {}),
  };

  const { data: metricsResponse, isLoading: metricsLoading } = useApprovalMetrics();
  const { data: actionsResponse, isLoading: actionsLoading } = useApprovalActions(params);
  const { data: historyResponse, isLoading: historyLoading } = useApprovalActions({
    offset: 0,
    limit: 50,
  });
  const { data: deepLinkedAction } = useApprovalAction(actionIdParam ?? "");
  const expireMutation = useExpireStaleActions();

  // Open dialog when ?action=<id> is present and the action data loads
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => {
    if (actionIdParam && deepLinkedAction?.data) {
      setSelectedAction(deepLinkedAction.data);
      setDialogOpen(true);
    }
  }, [actionIdParam, deepLinkedAction]);

  const metrics = metricsResponse?.data;
  const actions = actionsResponse?.data ?? [];
  const meta = actionsResponse?.meta;
  const total = meta?.total ?? 0;
  const hasMore = meta?.has_more ?? false;

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  function handleFilterChange(key: keyof FilterState, value: string) {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(0);
  }

  function handleClearFilters() {
    setFilters(EMPTY_FILTERS);
    setPage(0);
  }

  function handleActionClick(action: ApprovalAction) {
    setSelectedAction(action);
    setDialogOpen(true);
    setSearchParams((prev) => {
      prev.set("action", action.id);
      return prev;
    });
  }

  function handleDialogClose(open: boolean) {
    setDialogOpen(open);
    if (!open) {
      setSearchParams((prev) => {
        prev.delete("action");
        return prev;
      });
    }
  }

  function handleExpireStale() {
    if (confirm("Expire all stale pending actions?")) {
      expireMutation.mutate({});
    }
  }

  const historyActions = (historyResponse?.data ?? [])
    .filter((a) => RESOLVED_STATUSES.has(a.status))
    .slice(0, HISTORY_LIMIT);

  const hasActiveFilters =
    filters.tool_name !== "" || filters.status !== "pending" || filters.butler !== "";

  return (
    <div className="space-y-6">
      {/* Page heading */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Approvals</h1>
          <p className="text-muted-foreground mt-1">
            Manage approval-gated actions and standing rules.
          </p>
        </div>
        <Button variant="outline" asChild>
          <Link to="/approvals/rules">Standing Rules</Link>
        </Button>
      </div>

      {/* Metrics */}
      {metrics && !metricsLoading && <ApprovalMetricsBar metrics={metrics} />}

      {/* Filters */}
      <Card>
        <CardHeader>
          <CardTitle>Filter Actions</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-4">
            <div>
              <label className="text-sm font-medium">Tool Name</label>
              <Input
                placeholder="Filter by tool..."
                value={filters.tool_name}
                onChange={(e) => handleFilterChange("tool_name", e.target.value)}
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-sm font-medium">Status</label>
              <Select
                value={filters.status}
                onValueChange={(value) => handleFilterChange("status", value)}
              >
                <SelectTrigger className="mt-1">
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
            <div>
              <label className="text-sm font-medium">Butler</label>
              <Input
                placeholder="Filter by butler..."
                value={filters.butler}
                onChange={(e) => handleFilterChange("butler", e.target.value)}
                className="mt-1"
              />
            </div>
            <div className="flex items-end gap-2">
              {hasActiveFilters && (
                <Button variant="outline" onClick={handleClearFilters}>
                  Clear
                </Button>
              )}
              <Button variant="outline" onClick={handleExpireStale}>
                Expire Stale
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Actions table */}
      <Card>
        <CardHeader>
          <CardTitle>
            Actions
            {total > 0 && (
              <span className="ml-2 text-sm font-normal text-muted-foreground">
                ({rangeStart}–{rangeEnd} of {total})
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {actionsLoading ? (
            <div className="text-center text-muted-foreground py-8">Loading...</div>
          ) : (
            <>
              <ActionTable actions={actions} onActionClick={handleActionClick} />

              {/* Pagination */}
              {total > PAGE_SIZE && (
                <div className="flex items-center justify-between mt-4">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                    disabled={page === 0}
                  >
                    Previous
                  </Button>
                  <span className="text-sm text-muted-foreground">
                    Page {page + 1} of {Math.ceil(total / PAGE_SIZE)}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setPage((p) => p + 1)}
                    disabled={!hasMore}
                  >
                    Next
                  </Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* History */}
      <Card>
        <CardHeader>
          <CardTitle>History</CardTitle>
        </CardHeader>
        <CardContent>
          {historyLoading ? (
            <div className="text-center text-muted-foreground py-8">Loading...</div>
          ) : (
            <HistoryTable actions={historyActions} onActionClick={handleActionClick} />
          )}
        </CardContent>
      </Card>

      {/* Action detail dialog */}
      <ActionDetailDialog
        action={selectedAction}
        open={dialogOpen}
        onOpenChange={handleDialogClose}
      />
    </div>
  );
}
