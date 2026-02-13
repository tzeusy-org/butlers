/**
 * RegistryTable — table showing switchboard butler registry.
 *
 * Features:
 * - Table: Name, Endpoint URL, Modules (badges), Description, Last Seen (relative time)
 * - Loading skeleton, empty state
 */

import { formatDistanceToNow } from "date-fns";

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
import { useRegistry } from "@/hooks/use-general";

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-28" /></TableCell>
          <TableCell><Skeleton className="h-4 w-48" /></TableCell>
          <TableCell><Skeleton className="h-4 w-32" /></TableCell>
          <TableCell><Skeleton className="h-4 w-40" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-sm text-muted-foreground">
      <p>No butlers registered in the switchboard.</p>
    </div>
  );
}

const MAX_MODULE_PARSE_DEPTH = 10;

function splitModuleString(rawModules: string): string[] {
  return rawModules
    .split(",")
    .map((moduleName) => moduleName.trim())
    .filter((moduleName) => moduleName.length > 0);
}

function normalizeModules(rawModules: unknown, depth = 0): string[] {
  if (depth > MAX_MODULE_PARSE_DEPTH) {
    return [];
  }

  if (Array.isArray(rawModules)) {
    return rawModules
      .flatMap((moduleName) => normalizeModules(moduleName, depth + 1));
  }

  if (typeof rawModules !== "string") {
    return [];
  }

  const trimmed = rawModules.trim();
  if (!trimmed) {
    return [];
  }

  if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
    try {
      return normalizeModules(JSON.parse(trimmed), depth + 1);
    } catch {
      const stripped = trimmed.slice(1, -1).trim();
      return stripped ? splitModuleString(stripped) : [];
    }
  }

  return splitModuleString(trimmed);
}

// ---------------------------------------------------------------------------
// RegistryTable
// ---------------------------------------------------------------------------

export default function RegistryTable() {
  const { data: response, isLoading } = useRegistry();

  const entries = response?.data ?? [];

  return (
    <div className="space-y-4">
      {!isLoading && entries.length === 0 ? (
        <EmptyState />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Endpoint</TableHead>
              <TableHead>Modules</TableHead>
              <TableHead>Description</TableHead>
              <TableHead>Last Seen</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <SkeletonRows />
            ) : (
              entries.map((entry) => {
                const modules = normalizeModules(entry.modules);

                return (
                  <TableRow key={entry.name}>
                  <TableCell className="font-medium">{entry.name}</TableCell>
                  <TableCell>
                    <code className="text-xs text-muted-foreground">
                      {entry.endpoint_url}
                    </code>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {modules.length > 0 ? (
                        modules.map((mod, idx) => (
                          <Badge key={`${mod}-${idx}`} variant="secondary" className="text-xs">
                            {mod}
                          </Badge>
                        ))
                      ) : (
                        <span className="text-xs text-muted-foreground">{"\u2014"}</span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="max-w-xs truncate text-sm text-muted-foreground">
                    {entry.description ?? "\u2014"}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                    {entry.last_seen_at
                      ? formatDistanceToNow(new Date(entry.last_seen_at), {
                          addSuffix: true,
                        })
                      : "\u2014"}
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
