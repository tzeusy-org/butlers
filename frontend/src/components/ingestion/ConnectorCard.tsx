/**
 * Connector card for the Connectors tab grid.
 *
 * Shows: type icon label, endpoint identity, liveness badge, health state,
 * today's ingestion count, last heartbeat age, and backfill-active indicator.
 * Clicking the card navigates to /ingestion/connectors/:type/:identity.
 * Includes a delete button (with confirmation) for stale/duplicate connectors.
 */

import { useState } from "react";
import { Link } from "react-router";
import { formatDistanceToNow } from "date-fns";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { LivenessBadge } from "./LivenessBadge";
import { useDeleteConnector } from "@/hooks/use-ingestion";
import type { ConnectorSummary } from "@/api/index.ts";

interface ConnectorCardProps {
  connector: ConnectorSummary;
  hasActiveBackfill?: boolean;
}

export function ConnectorCard({
  connector,
  hasActiveBackfill = false,
}: ConnectorCardProps) {
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const deleteMutation = useDeleteConnector();

  const href = `/ingestion/connectors/${encodeURIComponent(connector.connector_type)}/${encodeURIComponent(connector.endpoint_identity)}`;

  const todayIngested = connector.today?.messages_ingested ?? 0;
  const uptimePct = connector.today?.uptime_pct;

  const lastSeen = connector.last_heartbeat_at
    ? formatDistanceToNow(new Date(connector.last_heartbeat_at), {
        addSuffix: true,
      })
    : "never";

  return (
    <>
      <Link to={href} className="block" data-testid="connector-card">
        <Card className="transition-shadow hover:shadow-md">
          <CardHeader className="pb-2">
            <div className="flex items-start justify-between gap-2">
              <div>
                <CardTitle className="text-sm font-semibold">
                  {connector.connector_type}
                </CardTitle>
                <CardDescription className="font-mono text-xs">
                  {connector.endpoint_identity}
                </CardDescription>
              </div>
              <div className="flex flex-col gap-1 items-end">
                <div className="flex items-center gap-1">
                  <LivenessBadge
                    liveness={connector.liveness}
                    state={connector.state}
                    showState
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6 text-muted-foreground hover:text-destructive"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      setShowDeleteDialog(true);
                    }}
                    title="Delete connector"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
                {hasActiveBackfill && (
                  <Badge variant="secondary" className="text-xs">
                    backfill active
                  </Badge>
                )}
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Today ingested</dt>
              <dd className="text-right font-medium tabular-nums">
                {todayIngested.toLocaleString()}
              </dd>

              {uptimePct != null && (
                <>
                  <dt className="text-muted-foreground">Uptime</dt>
                  <dd className="text-right tabular-nums">{uptimePct.toFixed(1)}%</dd>
                </>
              )}

              <dt className="text-muted-foreground">Last seen</dt>
              <dd className="text-right text-xs text-muted-foreground">{lastSeen}</dd>
            </dl>
          </CardContent>
        </Card>
      </Link>

      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete connector?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently remove{" "}
              <strong>
                {connector.connector_type}/{connector.endpoint_identity}
              </strong>{" "}
              from the registry and delete its heartbeat history. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={async () => {
                try {
                  await deleteMutation.mutateAsync({
                    connectorType: connector.connector_type,
                    endpointIdentity: connector.endpoint_identity,
                  });
                  toast.success(
                    `Deleted ${connector.connector_type}/${connector.endpoint_identity}`,
                  );
                } catch (err) {
                  toast.error(
                    `Delete failed: ${err instanceof Error ? err.message : "Unknown error"}`,
                  );
                }
                setShowDeleteDialog(false);
              }}
            >
              {deleteMutation.isPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
