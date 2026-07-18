import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface TableSkeletonProps {
  /** Number of skeleton rows to display. @default 5 */
  rows?: number;
  /** Column definitions: each entry is a width class (e.g. "w-24"). */
  columns: { width: string; alignRight?: boolean }[];
  /** Accessible column names retained while visual headers are loading. */
  headers?: string[];
}

/**
 * Generic skeleton loader for table-based data displays.
 *
 * Renders a table with skeleton placeholders for each cell, matching
 * the layout of the real table it will replace once data loads.
 */
export function TableSkeleton({ rows = 5, columns, headers }: TableSkeletonProps) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {columns.map((col, i) => (
            <TableHead key={i} className={col.alignRight ? "text-right" : ""}>
              {headers?.[i] ? <span className="sr-only">{headers[i]}</span> : null}
              <Skeleton className="h-4 w-16" />
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {Array.from({ length: rows }, (_, rowIdx) => (
          <TableRow key={rowIdx}>
            {columns.map((col, colIdx) => (
              <TableCell
                key={colIdx}
                className={col.alignRight ? "text-right" : ""}
              >
                <Skeleton
                  className={`h-4 ${col.width} ${col.alignRight ? "ml-auto" : ""}`}
                />
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** Pre-configured skeleton matching the notification feed table layout. */
export function NotificationTableSkeleton({
  rows = 5,
  hasTriageControls = false,
}: {
  rows?: number;
  hasTriageControls?: boolean;
}) {
  const columns = [
    { width: "w-14" },
    { width: "w-24" },
    { width: "w-24" },
    { width: "w-16" },
    { width: "w-48" },
    { width: "w-20", alignRight: true },
  ];
  const headers = ["Status", "Butler", "Recipient", "Channel", "Message", "Time"];

  if (hasTriageControls) {
    columns.push({ width: "w-20", alignRight: true });
    headers.push("Actions");
  }

  return (
    <TableSkeleton
      rows={rows}
      columns={columns}
      headers={headers}
    />
  );
}
