/**
 * Fanout matrix table: rows = connectors, cols = butlers, cells = message count.
 * Shared between Overview and Connectors tabs.
 */

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { ConnectorFanout } from "@/api/index.ts";

interface FanoutMatrixProps {
  fanout: ConnectorFanout | undefined;
  isLoading: boolean;
}

export function FanoutMatrix({ fanout, isLoading }: FanoutMatrixProps) {
  // Collect all unique butler names (columns)
  const butlers = Array.from(
    new Set(
      (fanout?.matrix ?? []).flatMap((row) => Object.keys(row.targets)),
    ),
  ).sort();

  const rows = fanout?.matrix ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Connector x Butler Fanout</CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : rows.length === 0 ? (
          <div className="flex h-24 items-center justify-center text-sm text-muted-foreground">
            No fanout data available
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="whitespace-nowrap">Connector</TableHead>
                {butlers.map((b) => (
                  <TableHead key={b} className="whitespace-nowrap text-right">
                    {b}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => {
                const label = `${row.connector_type}:${row.endpoint_identity}`;
                return (
                  <TableRow key={label}>
                    <TableCell className="font-mono text-xs whitespace-nowrap">{label}</TableCell>
                    {butlers.map((b) => (
                      <TableCell
                        key={b}
                        className="text-right tabular-nums text-sm"
                      >
                        {row.targets[b]?.toLocaleString() ?? "—"}
                      </TableCell>
                    ))}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
