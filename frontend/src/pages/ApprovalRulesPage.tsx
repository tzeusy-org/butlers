import { useState } from "react";
import type { ApprovalRule, ApprovalRuleParams } from "@/api/types";
import { RulesTable } from "@/components/approvals/rules-table";
import { RuleDetailDialog } from "@/components/approvals/rule-detail-dialog";
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
import { useApprovalRules } from "@/hooks/use-approvals";

const PAGE_SIZE = 20;

const ACTIVE_OPTIONS = [
  { value: "all", label: "All rules" },
  { value: "true", label: "Active only" },
  { value: "false", label: "Inactive only" },
] as const;

interface FilterState {
  tool_name: string;
  active: string;
  butler: string;
}

const EMPTY_FILTERS: FilterState = {
  tool_name: "",
  active: "true",
  butler: "",
};

export default function ApprovalRulesPage() {
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [page, setPage] = useState(0);
  const [selectedRule, setSelectedRule] = useState<ApprovalRule | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  const params: ApprovalRuleParams = {
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    ...(filters.tool_name ? { tool_name: filters.tool_name } : {}),
    ...(filters.active !== "all" ? { active: filters.active === "true" } : {}),
    ...(filters.butler ? { butler: filters.butler } : {}),
  };

  const { data: rulesResponse, isLoading: rulesLoading } = useApprovalRules(params);

  const rules = rulesResponse?.data ?? [];
  const meta = rulesResponse?.meta;
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

  function handleRuleClick(rule: ApprovalRule) {
    setSelectedRule(rule);
    setDialogOpen(true);
  }

  const hasActiveFilters =
    filters.tool_name !== "" || filters.active !== "true" || filters.butler !== "";

  return (
    <div className="space-y-6">
      {/* Page heading */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Approval Rules</h1>
        <p className="text-muted-foreground mt-1">
          Manage standing approval rules for automatic action approval.
        </p>
      </div>

      {/* Filters */}
      <Card>
        <CardHeader>
          <CardTitle>Filter Rules</CardTitle>
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
                value={filters.active}
                onValueChange={(value) => handleFilterChange("active", value)}
              >
                <SelectTrigger className="mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ACTIVE_OPTIONS.map((opt) => (
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
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Rules table */}
      <Card>
        <CardHeader>
          <CardTitle>
            Rules
            {total > 0 && (
              <span className="ml-2 text-sm font-normal text-muted-foreground">
                ({rangeStart}–{rangeEnd} of {total})
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {rulesLoading ? (
            <div className="text-center text-muted-foreground py-8">Loading...</div>
          ) : (
            <>
              <RulesTable rules={rules} onRuleClick={handleRuleClick} />

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

      {/* Rule detail dialog */}
      <RuleDetailDialog rule={selectedRule} open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}
