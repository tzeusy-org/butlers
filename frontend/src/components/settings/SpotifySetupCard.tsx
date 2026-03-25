/**
 * SpotifySetupCard — connection status card for the settings page.
 *
 * Displays the Spotify account link state with a color-coded health badge.
 * States:
 * - connected (green badge): display name, account type, last sync, Disconnect button
 * - error (red badge): Re-connect button with error message
 * - not_configured (outline badge): client_id input + Connect button
 * - disconnected (amber/secondary badge): Connect button
 */

import { useState } from "react";

import { toast } from "sonner";

import type { SpotifyState } from "@/api/index.ts";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useSpotifyConfig,
  useSpotifyDisconnect,
  useSpotifyOAuthStart,
  useSpotifyStatus,
} from "@/hooks/use-spotify";

// ---------------------------------------------------------------------------
// Badge helpers
// ---------------------------------------------------------------------------

function stateBadgeVariant(
  state: SpotifyState,
): "default" | "secondary" | "destructive" | "outline" {
  switch (state) {
    case "connected":
      return "default";
    case "disconnected":
      return "secondary";
    case "error":
      return "destructive";
    case "not_configured":
      return "outline";
    default:
      return "outline";
  }
}

function stateBadgeLabel(state: SpotifyState): string {
  switch (state) {
    case "connected":
      return "Connected";
    case "disconnected":
      return "Disconnected";
    case "error":
      return "Error";
    case "not_configured":
      return "Not configured";
    default:
      return state;
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// ---------------------------------------------------------------------------
// SpotifyClientIdInput — client ID configuration input
// ---------------------------------------------------------------------------

function SpotifyClientIdInput({ onSaved }: { onSaved: () => void }) {
  const [clientId, setClientId] = useState("");
  const configMutation = useSpotifyConfig();

  const isValidClientId = /^[0-9a-f]{32}$/i.test(clientId.trim());

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!isValidClientId) return;
    try {
      await configMutation.mutateAsync({ client_id: clientId.trim() });
      toast.success("Spotify client ID saved");
      onSaved();
    } catch {
      toast.error("Failed to save client ID");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <p className="text-sm text-muted-foreground">
        To connect Spotify, you need a Spotify Developer app. Create one at{" "}
        <a
          href="https://developer.spotify.com/dashboard"
          target="_blank"
          rel="noopener noreferrer"
          className="underline underline-offset-2"
        >
          developer.spotify.com/dashboard
        </a>
        , then paste your app's Client ID below.
      </p>
      <div className="max-w-sm space-y-1">
        <Label htmlFor="spotify-client-id">Client ID</Label>
        <div className="flex gap-2">
          <Input
            id="spotify-client-id"
            placeholder="32-character hex string"
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            className="font-mono text-sm"
          />
          <Button
            type="submit"
            size="sm"
            disabled={!isValidClientId || configMutation.isPending}
          >
            {configMutation.isPending ? "Saving..." : "Save"}
          </Button>
        </div>
        {clientId.length > 0 && !isValidClientId && (
          <p className="text-xs text-destructive">
            Client ID must be a 32-character hexadecimal string.
          </p>
        )}
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// SpotifyConnectButton — OAuth PKCE flow trigger
// ---------------------------------------------------------------------------

function SpotifyConnectButton({ label = "Connect Spotify" }: { label?: string }) {
  const oauthMutation = useSpotifyOAuthStart();

  async function handleConnect() {
    try {
      const result = await oauthMutation.mutateAsync();
      window.location.href = result.authorization_url;
    } catch {
      toast.error("Failed to start Spotify authorization");
    }
  }

  return (
    <Button size="sm" onClick={handleConnect} disabled={oauthMutation.isPending}>
      {oauthMutation.isPending ? "Redirecting..." : label}
    </Button>
  );
}

// ---------------------------------------------------------------------------
// DisconnectDialog — confirmation dialog for disconnect flow
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
          <DialogTitle>Disconnect Spotify?</DialogTitle>
          <DialogDescription>
            {"Your Spotify credentials will be removed. You'll need to re-authorize to reconnect."}
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
// SpotifySetupCard — main component
// ---------------------------------------------------------------------------

export function SpotifySetupCard() {
  const statusQuery = useSpotifyStatus();
  const disconnectMutation = useSpotifyDisconnect();

  const [showClientIdInput, setShowClientIdInput] = useState(false);
  const [disconnectDialogOpen, setDisconnectDialogOpen] = useState(false);

  const status = statusQuery.data;
  const displayState: SpotifyState = status?.state ?? "not_configured";

  async function handleDisconnect() {
    try {
      await disconnectMutation.mutateAsync();
      toast.success("Spotify disconnected");
      setDisconnectDialogOpen(false);
    } catch {
      toast.error("Failed to disconnect Spotify");
    }
  }

  if (statusQuery.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Spotify</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-16 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (statusQuery.isError) {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Spotify</CardTitle>
            </div>
            <Badge variant="destructive">Error</Badge>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-destructive">
            Failed to fetch Spotify status. Please refresh to try again.
          </p>
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
              <CardTitle>Spotify</CardTitle>
              <CardDescription className="mt-1">
                Connect your Spotify account to let butlers track your listening history and
                sessions. Read-only — butlers will not control playback.
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
              {status.display_name && (
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground">Account</span>
                  <span>{status.display_name}</span>
                </div>
              )}
              {status.account_type && (
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground">Type</span>
                  <span className="capitalize">{status.account_type}</span>
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

          {/* Error state: show error message */}
          {displayState === "error" && status?.error && (
            <p className="text-sm text-destructive">{status.error}</p>
          )}

          {/* not_configured: show client ID setup */}
          {displayState === "not_configured" && (
            <>
              {showClientIdInput ? (
                <SpotifyClientIdInput onSaved={() => setShowClientIdInput(false)} />
              ) : (
                <p className="text-sm text-muted-foreground">
                  No Spotify account linked. Configure your Spotify app credentials and then
                  authorize access.
                </p>
              )}
            </>
          )}

          {/* Action buttons */}
          <div className="flex flex-wrap items-center gap-2">
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

            {displayState === "error" && (
              <SpotifyConnectButton label="Re-connect Spotify" />
            )}

            {displayState === "not_configured" && !showClientIdInput && (
              <Button size="sm" variant="outline" onClick={() => setShowClientIdInput(true)}>
                Set Client ID
              </Button>
            )}

            {(displayState === "not_configured" || displayState === "disconnected") && (
              <SpotifyConnectButton />
            )}
          </div>
        </CardContent>
      </Card>

      <DisconnectDialog
        open={disconnectDialogOpen}
        onOpenChange={setDisconnectDialogOpen}
        onConfirm={handleDisconnect}
        isPending={disconnectMutation.isPending}
      />
    </>
  );
}
