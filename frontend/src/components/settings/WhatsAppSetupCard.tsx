/**
 * WhatsAppSetupCard — connection status card for the settings page.
 *
 * Displays current WhatsApp connection state with a color-coded health badge.
 * Mirrors the pattern used by the Google OAuth section:
 * - connected (green badge): shows phone, paired date, last sync, Disconnect button
 * - pair_required (red badge): shows Re-pair button
 * - disconnected (amber badge): shows bridge-not-running tooltip
 * - not_configured (outline badge): shows Link WhatsApp Account button
 */

import { useState } from "react";

import { toast } from "sonner";

import type { WhatsAppState } from "@/api/index.ts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useWhatsAppDisconnect, useWhatsAppHealth, useWhatsAppStatus } from "@/hooks/use-whatsapp";

import { WhatsAppPairModal } from "./WhatsAppPairModal";

// ---------------------------------------------------------------------------
// Badge helpers
// ---------------------------------------------------------------------------

function stateBadgeVariant(
  state: WhatsAppState,
): "default" | "secondary" | "destructive" | "outline" {
  switch (state) {
    case "connected":
      return "default";
    case "disconnected":
      return "secondary";
    case "pair_required":
      return "destructive";
    case "not_configured":
      return "outline";
    default:
      return "outline";
  }
}

function stateBadgeLabel(state: WhatsAppState): string {
  switch (state) {
    case "connected":
      return "Connected";
    case "disconnected":
      return "Disconnected";
    case "pair_required":
      return "Pair required";
    case "not_configured":
      return "Not configured";
    default:
      return state;
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Disconnect confirmation dialog
// ---------------------------------------------------------------------------

function DisconnectDialog({
  open,
  onOpenChange,
  onConfirm,
  isPending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  isPending: boolean;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Disconnect WhatsApp?</DialogTitle>
          <DialogDescription>
            {"You'll need to re-scan the QR code to reconnect."}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isPending}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={isPending}>
            {isPending ? "Disconnecting..." : "Disconnect"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function WhatsAppSetupCard() {
  const statusQuery = useWhatsAppStatus();
  const healthQuery = useWhatsAppHealth();
  const disconnectMutation = useWhatsAppDisconnect();

  const [pairModalOpen, setPairModalOpen] = useState(false);
  const [disconnectDialogOpen, setDisconnectDialogOpen] = useState(false);

  const status = statusQuery.data;
  const health = healthQuery.data;

  // Use health data when available for live badge; fall back to status state
  const displayState: WhatsAppState =
    health?.state ?? status?.state ?? "not_configured";
  const bridgeRunning = health?.bridge_running ?? status?.bridge_running ?? false;

  async function handleDisconnect() {
    try {
      await disconnectMutation.mutateAsync();
      toast.success("WhatsApp disconnected");
      setDisconnectDialogOpen(false);
    } catch {
      toast.error("Failed to disconnect WhatsApp");
    }
  }

  if (statusQuery.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>WhatsApp</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-16 w-full" />
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>WhatsApp</CardTitle>
              <CardDescription className="mt-1">
                Connect your WhatsApp to give butlers awareness of your conversations.
                Read-only — butlers will not send messages.
              </CardDescription>
            </div>
            <Badge variant={stateBadgeVariant(displayState)}>
              {stateBadgeLabel(displayState)}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Connected state: show account details */}
          {displayState === "connected" && status && (
            <div className="space-y-1 text-sm">
              {status.phone && (
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground">Phone</span>
                  <span className="font-mono">{status.phone}</span>
                </div>
              )}
              {status.paired_at && (
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground">Paired</span>
                  <span>{formatDate(status.paired_at)}</span>
                </div>
              )}
              {status.last_sync_at && (
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground">Last sync</span>
                  <span>{formatDate(status.last_sync_at)}</span>
                </div>
              )}
            </div>
          )}

          {/* Bridge-not-running note for disconnected state */}
          {displayState === "disconnected" && !bridgeRunning && (
            <p className="text-sm text-muted-foreground">
              WhatsApp bridge is not running. The connector service may be stopped.
            </p>
          )}

          {/* not_configured description */}
          {displayState === "not_configured" && (
            <p className="text-sm text-muted-foreground">
              No WhatsApp account linked. Click the button below to scan a QR code
              with your phone to connect.
            </p>
          )}

          {/* Action buttons */}
          <div className="flex items-center gap-2">
            {displayState === "connected" && (
              <Button
                variant="outline"
                size="sm"
                className="text-destructive hover:bg-destructive/10"
                onClick={() => setDisconnectDialogOpen(true)}
              >
                Disconnect
              </Button>
            )}
            {(displayState === "not_configured" || displayState === "pair_required") && (
              <Button size="sm" onClick={() => setPairModalOpen(true)}>
                {displayState === "pair_required" ? "Re-pair Account" : "Link WhatsApp Account"}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      <WhatsAppPairModal
        open={pairModalOpen}
        onOpenChange={setPairModalOpen}
        onPaired={() => {
          setPairModalOpen(false);
          statusQuery.refetch();
          healthQuery.refetch();
        }}
      />

      <DisconnectDialog
        open={disconnectDialogOpen}
        onOpenChange={setDisconnectDialogOpen}
        onConfirm={handleDisconnect}
        isPending={disconnectMutation.isPending}
      />
    </>
  );
}
