/**
 * SteamSetupCard — Steam account management card for the settings page.
 *
 * Displays the list of connected Steam accounts with connect/disconnect actions
 * and a playtime analytics panel for the primary account.
 *
 * Sections:
 * - ConnectForm: API key + SteamID64 input → POST /api/steam/accounts
 * - AccountsList: list of connected accounts with status badges and disconnect
 * - PlaytimePanel: top games + recently played for the primary account
 */

import { useState } from "react";

import { Gamepad2, Trash2 } from "lucide-react";
import { toast } from "sonner";

import type { SteamAccountResponse } from "@/api/index.ts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
  useSteamAccounts,
  useSteamConnect,
  useSteamDisconnect,
  useSteamPlaytime,
} from "@/hooks/use-steam";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatMinutes(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

function statusBadgeVariant(
  status: SteamAccountResponse["status"],
): "default" | "secondary" | "destructive" | "outline" {
  switch (status) {
    case "active":
      return "default";
    case "suspended":
      return "secondary";
    case "revoked":
      return "destructive";
    default:
      return "outline";
  }
}

// ---------------------------------------------------------------------------
// ConnectSteamForm — API key + SteamID64 input form
// ---------------------------------------------------------------------------

function ConnectSteamForm({ onSuccess }: { onSuccess: () => void }) {
  const [apiKey, setApiKey] = useState("");
  const [steamId, setSteamId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const connectMutation = useSteamConnect();

  const isValidApiKey = /^[0-9A-Fa-f]{32}$/.test(apiKey.trim());
  const isValidSteamId = /^\d{17}$/.test(steamId.trim());
  const canSubmit = isValidApiKey && isValidSteamId && !connectMutation.isPending;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    try {
      await connectMutation.mutateAsync({
        api_key: apiKey.trim(),
        steam_id: steamId.trim(),
        display_name: displayName.trim() || null,
      });
      toast.success("Steam account connected");
      setApiKey("");
      setSteamId("");
      setDisplayName("");
      onSuccess();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to connect Steam account";
      toast.error(message);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Connect your Steam account using a{" "}
        <a
          href="https://steamcommunity.com/dev/apikey"
          target="_blank"
          rel="noopener noreferrer"
          className="underline underline-offset-2"
        >
          Steam Web API key
        </a>{" "}
        and your SteamID64.
      </p>
      <div className="max-w-sm space-y-3">
        <div className="space-y-1">
          <Label htmlFor="steam-api-key">API Key</Label>
          <Input
            id="steam-api-key"
            type="password"
            placeholder="32-character hex string"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            autoComplete="new-password"
            className="font-mono text-sm"
          />
          {apiKey.length > 0 && !isValidApiKey && (
            <p className="text-xs text-destructive">
              API key must be a 32-character hexadecimal string.
            </p>
          )}
        </div>
        <div className="space-y-1">
          <Label htmlFor="steam-id">SteamID64</Label>
          <Input
            id="steam-id"
            placeholder="17-digit number (e.g. 76561198000000000)"
            value={steamId}
            onChange={(e) => setSteamId(e.target.value)}
            className="font-mono text-sm"
          />
          {steamId.length > 0 && !isValidSteamId && (
            <p className="text-xs text-destructive">
              SteamID64 must be a 17-digit number. Find yours at{" "}
              <a
                href="https://www.steamidfinder.com"
                target="_blank"
                rel="noopener noreferrer"
                className="underline underline-offset-2"
              >
                steamidfinder.com
              </a>
              .
            </p>
          )}
        </div>
        <div className="space-y-1">
          <Label htmlFor="steam-display-name">Display Name (optional)</Label>
          <Input
            id="steam-display-name"
            placeholder="Leave blank to use Steam persona name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            className="text-sm"
          />
        </div>
        <Button type="submit" size="sm" disabled={!canSubmit}>
          {connectMutation.isPending ? "Connecting..." : "Connect account"}
        </Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// DisconnectDialog — confirmation before disconnecting an account
// ---------------------------------------------------------------------------

function DisconnectDialog({
  open,
  account,
  onOpenChange,
  onConfirm,
  isPending,
}: {
  open: boolean;
  account: SteamAccountResponse | null;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  isPending: boolean;
}) {
  const label = account?.display_name ?? `steam_id ${account?.steam_id}`;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Disconnect Steam account?</DialogTitle>
          <DialogDescription>
            <span className="font-medium">{label}</span> will be soft-revoked. The connector will
            stop polling this account. You can reconnect at any time.
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
// AccountRow — single account in the accounts list
// ---------------------------------------------------------------------------

function AccountRow({
  account,
  onDisconnect,
}: {
  account: SteamAccountResponse;
  onDisconnect: (account: SteamAccountResponse) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-2">
      <div className="flex min-w-0 items-center gap-3">
        {account.avatar_url && (
          <img
            src={account.avatar_url}
            alt={account.display_name ?? "Steam avatar"}
            className="h-8 w-8 rounded-sm shrink-0"
          />
        )}
        {!account.avatar_url && (
          <div className="h-8 w-8 rounded-sm bg-muted flex items-center justify-center shrink-0">
            <Gamepad2 className="h-4 w-4 text-muted-foreground" />
          </div>
        )}
        <div className="min-w-0">
          <p className="text-sm font-medium truncate">
            {account.display_name ?? `SteamID ${account.steam_id}`}
          </p>
          <p className="text-xs text-muted-foreground font-mono">{account.steam_id}</p>
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {account.is_primary && (
          <Badge variant="outline" className="text-xs">
            Primary
          </Badge>
        )}
        <Badge variant={statusBadgeVariant(account.status)} className="text-xs capitalize">
          {account.status}
        </Badge>
        {account.status !== "revoked" && (
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-destructive hover:bg-destructive/10 hover:text-destructive"
            title="Disconnect account"
            aria-label="Disconnect account"
            onClick={() => onDisconnect(account)}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PlaytimePanel — top games + recently played for the primary account
// ---------------------------------------------------------------------------

function PlaytimePanel({ primaryAccountId }: { primaryAccountId?: string }) {
  const [expanded, setExpanded] = useState(false);
  const playtimeQuery = useSteamPlaytime(primaryAccountId, expanded);

  if (!expanded) {
    return (
      <Button
        variant="ghost"
        size="sm"
        className="text-muted-foreground"
        onClick={() => setExpanded(true)}
      >
        Show playtime analytics
      </Button>
    );
  }

  if (playtimeQuery.isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (playtimeQuery.isError) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-destructive">
          Failed to load playtime analytics. The Steam profile may be private, or the API key may
          be invalid.
        </p>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => playtimeQuery.refetch()}>
            Retry
          </Button>
          <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={() => setExpanded(false)}>
            Hide
          </Button>
        </div>
      </div>
    );
  }

  const data = playtimeQuery.data;
  if (!data) return null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="space-y-0.5">
          <p className="text-sm font-medium">
            Playtime for {data.display_name ?? `SteamID ${data.steam_id}`}
          </p>
          <p className="text-xs text-muted-foreground">
            {data.total_games} games · {formatMinutes(data.total_playtime_minutes)} total
          </p>
        </div>
        <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={() => setExpanded(false)}>
          Hide
        </Button>
      </div>

      {data.top_games.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Top games
          </p>
          <div className="space-y-1">
            {data.top_games.map((game) => (
              <div key={game.app_id} className="flex items-center justify-between gap-2 text-sm">
                <span className="truncate">{game.name ?? `App ${game.app_id}`}</span>
                <span className="text-muted-foreground shrink-0 font-mono text-xs">
                  {formatMinutes(game.playtime_forever_minutes)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {data.recently_played.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Recently played (last 2 weeks)
          </p>
          <div className="space-y-1">
            {data.recently_played.map((game) => (
              <div key={game.app_id} className="flex items-center justify-between gap-2 text-sm">
                <span className="truncate">{game.name ?? `App ${game.app_id}`}</span>
                <span className="text-muted-foreground shrink-0 font-mono text-xs">
                  {game.playtime_2weeks_minutes != null
                    ? formatMinutes(game.playtime_2weeks_minutes)
                    : "—"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {data.top_games.length === 0 && data.recently_played.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No game playtime data available. The Steam profile may be set to private.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SteamSection — embeddable variant (no Card wrapper)
// ---------------------------------------------------------------------------

export function SteamSection() {
  const accountsQuery = useSteamAccounts();
  const disconnectMutation = useSteamDisconnect();

  const [showConnectForm, setShowConnectForm] = useState(false);
  const [disconnectTarget, setDisconnectTarget] = useState<SteamAccountResponse | null>(null);

  const accounts = accountsQuery.data?.accounts ?? [];
  const activeAccounts = accounts.filter((a) => a.status !== "revoked");
  const primaryAccount = activeAccounts.find((a) => a.is_primary) ?? activeAccounts[0];

  async function handleDisconnectConfirm() {
    if (!disconnectTarget) return;
    try {
      await disconnectMutation.mutateAsync(disconnectTarget.id);
      toast.success("Steam account disconnected");
      setDisconnectTarget(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to disconnect Steam account";
      toast.error(message);
    }
  }

  if (accountsQuery.isLoading) {
    return (
      <div>
        <h3 className="leading-none font-semibold">Steam</h3>
        <Skeleton className="mt-3 h-12 w-full" />
      </div>
    );
  }

  if (accountsQuery.isError) {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="leading-none font-semibold">Steam</h3>
          <Badge variant="destructive">Error</Badge>
        </div>
        <p className="text-sm text-destructive">
          Failed to fetch Steam accounts. Please refresh to try again.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="leading-none font-semibold">Steam</h3>
            <p className="text-muted-foreground text-sm mt-2">
              Connect your Steam account to let butlers track your game library and playtime.
              Read-only — butlers will not modify your account.
            </p>
          </div>
          {activeAccounts.length > 0 ? (
            <Badge variant="default">
              {activeAccounts.length === 1 ? "1 account" : `${activeAccounts.length} accounts`}
            </Badge>
          ) : (
            <Badge variant="outline">Not configured</Badge>
          )}
        </div>

        {activeAccounts.length > 0 && (
          <div className="divide-y divide-border rounded-md border">
            {activeAccounts.map((account) => (
              <div key={account.id} className="px-3">
                <AccountRow account={account} onDisconnect={setDisconnectTarget} />
              </div>
            ))}
          </div>
        )}

        {showConnectForm && (
          <ConnectSteamForm
            onSuccess={() => setShowConnectForm(false)}
          />
        )}

        <div className="flex flex-wrap items-center gap-2">
          {!showConnectForm && (
            <Button size="sm" variant="outline" onClick={() => setShowConnectForm(true)}>
              {activeAccounts.length === 0 ? "Connect Steam" : "Add account"}
            </Button>
          )}
          {showConnectForm && (
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground"
              onClick={() => setShowConnectForm(false)}
            >
              Cancel
            </Button>
          )}
        </div>

        {primaryAccount && !showConnectForm && (
          <PlaytimePanel primaryAccountId={primaryAccount.id} />
        )}
      </div>

      <DisconnectDialog
        open={disconnectTarget !== null}
        account={disconnectTarget}
        onOpenChange={(open) => { if (!open) setDisconnectTarget(null); }}
        onConfirm={handleDisconnectConfirm}
        isPending={disconnectMutation.isPending}
      />
    </>
  );
}

