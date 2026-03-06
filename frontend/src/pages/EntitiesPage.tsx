import { useState } from "react";
import { Link } from "react-router";

import type { EntityParams } from "@/api/types";
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
import { useEntities } from "@/hooks/use-memory";

const PAGE_SIZE = 50;

const ENTITY_TYPES = ["", "person", "organization", "place", "other"] as const;
const TYPE_LABELS: Record<string, string> = {
  "": "All Types",
  person: "Person",
  organization: "Organization",
  place: "Place",
  other: "Other",
};

function entityTypeBadgeVariant(
  entityType: string,
): "default" | "secondary" | "outline" {
  switch (entityType) {
    case "person":
      return "default";
    case "organization":
      return "secondary";
    default:
      return "outline";
  }
}

export default function EntitiesPage() {
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [page, setPage] = useState(0);

  const params: EntityParams = {
    q: search || undefined,
    entity_type: typeFilter || undefined,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data, isLoading } = useEntities(params);
  const entities = data?.data ?? [];
  const total = data?.meta?.total ?? 0;
  const hasMore =
    entities.length === PAGE_SIZE && (page + 1) * PAGE_SIZE < total;

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  function handleSearchChange(value: string) {
    setSearch(value);
    setPage(0);
  }

  function handleTypeChange(value: string) {
    setTypeFilter(value);
    setPage(0);
  }

  return (
    <div className="space-y-6">
      {/* Page heading */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Entities</h1>
        <p className="text-muted-foreground mt-1">
          Browse the knowledge graph — people, organizations, places, and more.
        </p>
      </div>

      {/* Entity table */}
      <Card>
        <CardHeader>
          <CardTitle>All Entities</CardTitle>
          <CardDescription>
            {total > 0
              ? `${total.toLocaleString()} entit${total !== 1 ? "ies" : "y"}`
              : ""}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {/* Filters */}
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <Input
              placeholder="Search entities..."
              value={search}
              onChange={(e) => handleSearchChange(e.target.value)}
              className="max-w-sm"
            />
            <select
              value={typeFilter}
              onChange={(e) => handleTypeChange(e.target.value)}
              className="rounded-md border border-input bg-background px-3 py-2 text-sm"
            >
              {ENTITY_TYPES.map((t) => (
                <option key={t} value={t}>
                  {TYPE_LABELS[t]}
                </option>
              ))}
            </select>
          </div>

          {/* Table */}
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : entities.length === 0 ? (
            <p className="text-muted-foreground py-8 text-center text-sm">
              No entities found.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-2 pr-4 font-medium">Name</th>
                    <th className="pb-2 pr-4 font-medium">Type</th>
                    <th className="pb-2 pr-4 font-medium">Aliases</th>
                    <th className="pb-2 pr-4 font-medium text-right">Facts</th>
                    <th className="pb-2 pr-4 font-medium">Contact</th>
                    <th className="pb-2 font-medium">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {entities.map((entity) => (
                    <tr
                      key={entity.id}
                      className="border-b last:border-0 hover:bg-muted/50"
                    >
                      <td className="py-2 pr-4">
                        <span className="inline-flex items-center gap-2">
                          <Link
                            to={`/entities/${entity.id}`}
                            className="font-medium text-primary hover:underline"
                          >
                            {entity.canonical_name}
                          </Link>
                          {entity.roles?.includes("owner") && (
                            <Badge
                              style={{ backgroundColor: "#7c3aed", color: "#fff" }}
                              className="text-xs"
                            >
                              Owner
                            </Badge>
                          )}
                        </span>
                      </td>
                      <td className="py-2 pr-4">
                        <Badge variant={entityTypeBadgeVariant(entity.entity_type)}>
                          {entity.entity_type}
                        </Badge>
                      </td>
                      <td className="py-2 pr-4 text-muted-foreground">
                        {entity.aliases.length > 0
                          ? entity.aliases.slice(0, 3).join(", ") +
                            (entity.aliases.length > 3
                              ? ` +${entity.aliases.length - 3}`
                              : "")
                          : "\u2014"}
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums">
                        {entity.fact_count}
                      </td>
                      <td className="py-2 pr-4">
                        {entity.linked_contact_id ? (
                          <Link
                            to={`/contacts/${entity.linked_contact_id}`}
                            className="text-primary hover:underline"
                          >
                            Linked
                          </Link>
                        ) : (
                          <span className="text-muted-foreground">{"\u2014"}</span>
                        )}
                      </td>
                      <td className="py-2 text-muted-foreground">
                        {new Date(entity.created_at).toLocaleDateString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Pagination */}
      {total > 0 && (
        <div className="flex items-center justify-between">
          <p className="text-muted-foreground text-sm">
            Showing {rangeStart}&ndash;{rangeEnd} of {total.toLocaleString()}
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
