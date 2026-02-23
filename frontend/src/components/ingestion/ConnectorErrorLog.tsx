/**
 * Error log panel: connectors in degraded or error state.
 * Derived from the connector list (liveness + state + error_message).
 */

import { formatDistanceToNow } from "date-fns";

import { Badge } from "@/components/ui/badge";
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
import type { ConnectorSummary } from "@/api/index.ts";

interface ConnectorErrorLogProps {
  connectors: ConnectorSummary[];
  isLoading: boolean;
}

export function ConnectorErrorLog({
  connectors,
  isLoading,
}: ConnectorErrorLogProps) {
  const errorConnectors = connectors.filter(
    (c) => c.state === "degraded" || c.state === "error" || c.liveness === "offline",
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>Error Log</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : errorConnectors.length === 0 ? (
          <p className="text-sm text-muted-foreground py-4 text-center">
            No connector errors detected.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Last seen</TableHead>
                <TableHead>Connector</TableHead>
                <TableHead>State</TableHead>
                <TableHead>Message</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {errorConnectors.map((c) => {
                const identity = `${c.connector_type}:${c.endpoint_identity}`;
                const lastSeen = c.last_heartbeat_at
                  ? formatDistanceToNow(new Date(c.last_heartbeat_at), {
                      addSuffix: true,
                    })
                  : "never";
                return (
                  <TableRow key={identity}>
                    <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                      {lastSeen}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{identity}</TableCell>
                    <TableCell>
                      <Badge
                        variant={c.state === "error" ? "destructive" : "outline"}
                        className="text-xs"
                      >
                        {c.state}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground max-w-xs truncate">
                      {c.error_message ?? "—"}
                    </TableCell>
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
