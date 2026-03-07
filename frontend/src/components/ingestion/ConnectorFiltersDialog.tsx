/**
 * ConnectorFiltersDialog
 *
 * Dialog for viewing and toggling source filter assignments on a connector.
 * Opens from ConnectorCard (small Filters button) or ConnectorDetailPage header.
 *
 * UX:
 * - Lists ALL named filters in a table: Name | Type | Mode | Patterns | Enabled
 * - Each row has a checkbox in the Enabled column
 * - Compatible / incompatible filters are shown (incompatible ones are greyed and disabled)
 * - Save calls PUT /connectors/{type}/{identity}/filters with the updated list
 * - "Manage Filters" link at the bottom navigates to the Filters management panel
 */

import { useState, useEffect } from "react";
import { Link } from "react-router";
import { Filter } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useConnectorFilters, useUpdateConnectorFilters } from "@/hooks/use-ingestion";
import type { ConnectorFilterAssignment } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ConnectorFiltersDialogProps {
  connectorType: string;
  endpointIdentity: string;
  /** Render target for the trigger button (defaults to a compact "Filters" button). */
  triggerVariant?: "card" | "page";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ConnectorFiltersDialog({
  connectorType,
  endpointIdentity,
  triggerVariant = "card",
}: ConnectorFiltersDialogProps) {
  const [open, setOpen] = useState(false);

  const { data: filtersResp, isLoading } = useConnectorFilters(
    open ? connectorType : null,
    open ? endpointIdentity : null,
  );

  // Always fetch filter count for badge (without loading full dialog data)
  const { data: badgeResp } = useConnectorFilters(connectorType, endpointIdentity);

  const filters = filtersResp?.data ?? [];
  const activeCount = (badgeResp?.data ?? []).filter((f) => f.enabled).length;

  // Local enabled state — initialised from server data whenever dialog opens.
  const [enabledMap, setEnabledMap] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (filters.length > 0) {
      const init: Record<string, boolean> = {};
      for (const f of filters) {
        init[f.filter_id] = f.enabled;
      }
      setEnabledMap(init);
    }
  }, [filters]);

  const mutation = useUpdateConnectorFilters(connectorType, endpointIdentity);

  function handleToggle(filterId: string, checked: boolean) {
    setEnabledMap((prev) => ({ ...prev, [filterId]: checked }));
  }

  function handleSave() {
    // Build the assignment list: only include filters that are enabled.
    // Disabled filters are explicitly sent with enabled=false so the server
    // can keep or remove their priority; sending them all lets the server
    // do a clean atomic replace.
    const assignments = filters
      .filter((f) => !f.incompatible)
      .map((f) => ({
        filter_id: f.filter_id,
        enabled: enabledMap[f.filter_id] ?? f.enabled,
        priority: f.priority,
      }));

    mutation.mutate(assignments, {
      onSuccess: () => setOpen(false),
    });
  }

  // Determine if local state differs from server state
  const hasChanges = filters.some(
    (f) => (enabledMap[f.filter_id] ?? f.enabled) !== f.enabled,
  );

  const triggerButton =
    triggerVariant === "page" ? (
      <Button variant="outline" size="sm">
        <Filter className="mr-1 h-4 w-4" />
        Filters
        {activeCount > 0 && (
          <Badge variant="secondary" className="ml-1 text-xs px-1">
            {activeCount}
          </Badge>
        )}
      </Button>
    ) : (
      <Button
        variant="ghost"
        size="sm"
        className="h-7 px-2 text-xs"
        data-testid="connector-filters-button"
      >
        <Filter className="h-3 w-3 mr-1" />
        Filters
        {activeCount > 0 && (
          <Badge variant="secondary" className="ml-1 text-xs px-1 py-0">
            {activeCount}
          </Badge>
        )}
      </Button>
    );

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        asChild
        onClick={(e) => {
          e.stopPropagation();
          e.preventDefault();
        }}
      >
        {triggerButton}
      </DialogTrigger>
      <DialogContent
        className="sm:max-w-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <DialogHeader>
          <DialogTitle>Source Filters — {endpointIdentity}</DialogTitle>
          <DialogDescription>
            Toggle which source filters are active for this connector. Only
            compatible filters can be enabled.
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="space-y-2 py-4">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : filters.length === 0 ? (
          <div className="py-8 text-center text-sm text-muted-foreground">
            <p>No named source filters have been created yet.</p>
            <Link
              to="/ingestion?tab=filters"
              className="mt-2 inline-block text-primary underline-offset-4 hover:underline"
              onClick={() => setOpen(false)}
            >
              Manage Filters
            </Link>
          </div>
        ) : (
          <div className="overflow-auto max-h-[60vh]">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Mode</TableHead>
                  <TableHead className="text-right">Patterns</TableHead>
                  <TableHead className="text-center">Enabled</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filters.map((filter: ConnectorFilterAssignment) => (
                  <FilterRow
                    key={filter.filter_id}
                    filter={filter}
                    checked={enabledMap[filter.filter_id] ?? filter.enabled}
                    onToggle={handleToggle}
                  />
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        <DialogFooter className="flex-col sm:flex-row gap-2 sm:items-center sm:justify-between">
          <Link
            to="/ingestion?tab=filters"
            className="text-sm text-primary underline-offset-4 hover:underline"
            onClick={() => setOpen(false)}
          >
            Manage Filters
          </Link>
          <div className="flex gap-2 sm:justify-end">
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleSave}
              disabled={!hasChanges || mutation.isPending || isLoading}
            >
              {mutation.isPending ? "Saving…" : "Save"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// FilterRow sub-component
// ---------------------------------------------------------------------------

interface FilterRowProps {
  filter: ConnectorFilterAssignment;
  checked: boolean;
  onToggle: (filterId: string, checked: boolean) => void;
}

function FilterRow({ filter, checked, onToggle }: FilterRowProps) {
  const isDisabled = filter.incompatible;

  return (
    <TableRow
      className={isDisabled ? "opacity-50" : undefined}
      data-testid={`filter-row-${filter.filter_id}`}
    >
      <TableCell className="font-medium">
        {filter.name}
        {filter.incompatible && (
          <Badge variant="outline" className="ml-2 text-xs text-muted-foreground">
            incompatible
          </Badge>
        )}
      </TableCell>
      <TableCell className="text-xs font-mono">{filter.source_key_type}</TableCell>
      <TableCell>
        <Badge
          variant={filter.filter_mode === "blacklist" ? "destructive" : "secondary"}
          className="text-xs"
        >
          {filter.filter_mode}
        </Badge>
      </TableCell>
      <TableCell className="text-right tabular-nums text-sm">
        {filter.pattern_count}
      </TableCell>
      <TableCell className="text-center">
        <Checkbox
          checked={checked}
          disabled={isDisabled}
          onCheckedChange={(val) => onToggle(filter.filter_id, !!val)}
          aria-label={`Enable filter ${filter.name}`}
          data-testid={`filter-checkbox-${filter.filter_id}`}
        />
      </TableCell>
    </TableRow>
  );
}
