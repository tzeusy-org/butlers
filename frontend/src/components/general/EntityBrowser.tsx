import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";
/**
 * EntityBrowser — searchable, filterable table for General butler entities.
 *
 * Features:
 * - Search input for filtering entities
 * - Collection filter dropdown, tag filter
 * - Table showing: Collection (badge), Tags (badges), Data preview, Created
 * - Click a row to expand and show full data via JsonViewer
 * - Loading skeleton, empty state, pagination
 */

import { useState } from "react";

import type { GeneralCollection, GeneralEntity } from "@/api/types.ts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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

import JsonViewer from "./JsonViewer.tsx";
import { Time } from "@/components/ui/time";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface EntityBrowserProps {
  entities: GeneralEntity[];
  isLoading: boolean;
  /** Search query, controlled externally. */
  search: string;
  onSearchChange: (value: string) => void;
  /** Available collections for the filter dropdown. */
  collections: GeneralCollection[];
  /** Currently selected collection filter (id), or empty for all. */
  activeCollection: string;
  onCollectionFilter: (collectionId: string) => void;
  /** Currently selected tag filter, or empty for all. */
  activeTag: string;
  onTagFilter: (tag: string) => void;
  /** All unique tags across entities for the filter dropdown. */
  availableTags: string[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Compact JSON preview (single line, truncated). */
function jsonPreview(value: Record<string, unknown>, maxLen = 80): string {
  const str = JSON.stringify(value);
  return str.length > maxLen ? str.slice(0, maxLen) + "\u2026" : str;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-32" /></TableCell>
          <TableCell><Skeleton className="h-4 w-48" /></TableCell>
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

function EmptyState() {
  return (
    <EmptyStateUI
      title="No entities found"
      description="Entities will appear here as the General butler stores structured data."
    />
  );
}

// ---------------------------------------------------------------------------
// EntityBrowser
// ---------------------------------------------------------------------------

export default function EntityBrowser({
  entities,
  isLoading,
  search,
  onSearchChange,
  collections,
  activeCollection,
  onCollectionFilter,
  activeTag,
  onTagFilter,
  availableTags,
}: EntityBrowserProps) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  function toggleExpand(id: string) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  return (
    <div className="space-y-4">
      {/* Search + filters */}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder="Search entities..."
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          className="w-64"
        />

        <Select value={activeCollection} onValueChange={onCollectionFilter}>
          <SelectTrigger className="w-48">
            <SelectValue placeholder="All collections" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">All collections</SelectItem>
            {collections.map((col) => (
              <SelectItem key={col.id} value={col.id}>
                {col.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={activeTag} onValueChange={onTagFilter}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="All tags" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">All tags</SelectItem>
            {availableTags.map((tag) => (
              <SelectItem key={tag} value={tag}>
                {tag}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {(activeCollection || activeTag) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              onCollectionFilter("");
              onTagFilter("");
            }}
          >
            Clear filters
          </Button>
        )}
      </div>

      {/* Table or empty state */}
      {!isLoading && entities.length === 0 ? (
        <EmptyState />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Collection</TableHead>
              <TableHead>Tags</TableHead>
              <TableHead>Data</TableHead>
              <TableHead>Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <SkeletonRows />
            ) : (
              entities.map((entity) => {
                const isExpanded = expandedIds.has(entity.id);
                return (
                  <TableRow
                    key={entity.id}
                    className="cursor-pointer"
                    onClick={() => toggleExpand(entity.id)}
                  >
                    <TableCell className="align-top">
                      <Badge variant="outline" className="text-xs">
                        {entity.collection_name ?? entity.collection_id}
                      </Badge>
                    </TableCell>
                    <TableCell className="align-top">
                      <div className="flex flex-wrap gap-1">
                        {entity.tags.length > 0 ? (
                          entity.tags.map((tag) => (
                            <Badge
                              key={tag}
                              variant="secondary"
                              className="text-xs"
                            >
                              {tag}
                            </Badge>
                          ))
                        ) : (
                          <span className="text-xs text-muted-foreground">
                            {"\u2014"}
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="max-w-md align-top">
                      {isExpanded ? (
                        <div
                          className="rounded-md bg-muted p-3"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <JsonViewer data={entity.data} />
                        </div>
                      ) : (
                        <code className="text-xs text-muted-foreground">
                          {jsonPreview(entity.data)}
                        </code>
                      )}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm text-muted-foreground align-top">
                      <Time value={entity.created_at} mode="absolute" precision="day" compact />
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
