import { useState } from "react";

import type { OAuthCredentialState } from "@/api/index.ts";
import { getOAuthStartUrl } from "@/api/index.ts";
import { AutoRefreshToggle } from "@/components/ui/auto-refresh-toggle";
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
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAutoRefresh } from "@/hooks/use-auto-refresh";
import {
  useDeleteGoogleCredentials,
  useGoogleCredentialStatus,
} from "@/hooks/use-secrets";
import { useDarkMode } from "@/hooks/useDarkMode";
import { RECENT_SEARCHES_KEY } from "@/lib/local-settings";

type ThemeOption = "light" | "dark" | "system";

function getRecentSearchCount() {
  try {
    const raw = localStorage.getItem(RECENT_SEARCHES_KEY);
    if (!raw) return 0;
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.length : 0;
  } catch {
    return 0;
  }
}

// ---------------------------------------------------------------------------
// Google OAuth helpers
// ---------------------------------------------------------------------------

function healthBadgeVariant(
  state: OAuthCredentialState,
): "default" | "secondary" | "destructive" | "outline" {
  switch (state) {
    case "connected":
      return "default";
    case "not_configured":
      return "outline";
    case "expired":
    case "missing_scope":
    case "redirect_uri_mismatch":
    case "unapproved_tester":
    case "unknown_error":
      return "destructive";
    default:
      return "secondary";
  }
}

function healthBadgeLabel(state: OAuthCredentialState): string {
  switch (state) {
    case "connected":
      return "Connected";
    case "not_configured":
      return "Not configured";
    case "expired":
      return "Expired";
    case "missing_scope":
      return "Missing scope";
    case "redirect_uri_mismatch":
      return "Redirect URI mismatch";
    case "unapproved_tester":
      return "Unapproved tester";
    case "unknown_error":
      return "Unknown error";
    default:
      return state;
  }
}

function PresenceRow({
  label,
  present,
  value,
}: {
  label: string;
  present: boolean;
  value?: string | null;
}) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-border last:border-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <div className="flex items-center gap-2">
        {value && (
          <span className="text-sm font-mono text-foreground">{value}</span>
        )}
        <Badge variant={present ? "default" : "outline"}>
          {present ? "Configured" : "Not set"}
        </Badge>
      </div>
    </div>
  );
}

function DeleteCredentialsDialog() {
  const [open, setOpen] = useState(false);
  const deleteMutation = useDeleteGoogleCredentials();
  const [error, setError] = useState<string | null>(null);

  async function handleDelete() {
    setError(null);
    try {
      await deleteMutation.mutateAsync();
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete credentials.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="destructive" size="sm">
          Delete credentials
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete Google credentials?</DialogTitle>
          <DialogDescription>
            This will permanently remove all stored Google OAuth credentials
            (client_id, client_secret, and refresh token) from the database.
            The butler will no longer be able to access Google services until
            credentials are re-configured and the OAuth flow is re-run.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <p className="text-sm text-destructive">{error}</p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleDelete}
            disabled={deleteMutation.isPending}
          >
            {deleteMutation.isPending ? "Deleting..." : "Delete credentials"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Combined Google OAuth card
// ---------------------------------------------------------------------------

function GoogleOAuthCard() {
  const credStatusQuery = useGoogleCredentialStatus();
  const credStatus = credStatusQuery.data;
  const isLoading = credStatusQuery.isLoading;
  const isError = credStatusQuery.isError;
  const oauthStartUrl = getOAuthStartUrl();
  const canStartOAuth =
    credStatus?.client_id_configured && credStatus?.client_secret_configured;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle>Google OAuth</CardTitle>
            <CardDescription>
              Configure Google OAuth credentials and authorization flow for
              Calendars, Emails, and Contacts.
            </CardDescription>
          </div>
          {isLoading ? (
            <Skeleton className="h-6 w-24" />
          ) : isError ? (
            <Badge variant="destructive">Unavailable</Badge>
          ) : credStatus ? (
            <Badge variant={healthBadgeVariant(credStatus.oauth_health)}>
              {healthBadgeLabel(credStatus.oauth_health)}
            </Badge>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Credential presence */}
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : isError ? (
          <p className="text-sm text-destructive">
            Failed to load credential status. Ensure the dashboard API is running.
          </p>
        ) : credStatus ? (
          <>
            <PresenceRow
              label="Client ID"
              present={credStatus.client_id_configured}
            />
            <PresenceRow
              label="Client Secret"
              present={credStatus.client_secret_configured}
            />
            <PresenceRow
              label="Refresh Token"
              present={credStatus.refresh_token_present}
            />
            {credStatus.scope && (
              <div className="pt-2">
                <p className="text-xs text-muted-foreground">Granted scopes:</p>
                <p className="text-sm font-mono mt-0.5 break-all">{credStatus.scope}</p>
              </div>
            )}
            {credStatus.oauth_health_remediation && (
              <div className="pt-2 rounded-md bg-muted/50 p-3">
                <p className="text-sm text-muted-foreground">
                  {credStatus.oauth_health_remediation}
                </p>
                {credStatus.oauth_health_detail && (
                  <p className="text-xs text-muted-foreground mt-1 font-mono">
                    {credStatus.oauth_health_detail}
                  </p>
                )}
              </div>
            )}
          </>
        ) : null}

        <div className="border-t border-border" />

        {/* Connect / Re-connect */}
        <div className="flex items-center gap-4">
          <a
            href={oauthStartUrl}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => {
              if (!canStartOAuth) {
                e.preventDefault();
              }
            }}
          >
            <Button
              disabled={!canStartOAuth || isLoading}
              variant={credStatus?.refresh_token_present ? "outline" : "default"}
            >
              {credStatus?.refresh_token_present
                ? "Re-connect Google"
                : "Connect Google"}
            </Button>
          </a>
          {!canStartOAuth && !isLoading && (
            <p className="text-sm text-muted-foreground">
              Configure GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in
              Secrets before connecting.
            </p>
          )}
          {credStatus?.oauth_health === "connected" && (
            <p className="text-sm text-green-600 dark:text-green-400">
              Google account is connected and credentials are valid.
            </p>
          )}
        </div>

        <div className="border-t border-border" />

        {/* Danger zone */}
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-destructive">Danger zone</p>
            <p className="text-xs text-muted-foreground">
              Delete all stored Google OAuth credentials. This cannot be undone.
            </p>
          </div>
          <DeleteCredentialsDialog />
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function SettingsPage() {
  const { theme, resolvedTheme, setTheme } = useDarkMode();
  const autoRefreshControl = useAutoRefresh(10_000);

  const [recentSearchCount, setRecentSearchCount] = useState(getRecentSearchCount);

  function handleThemeChange(value: string) {
    setTheme(value as ThemeOption);
  }

  function clearRecentSearches() {
    try {
      localStorage.removeItem(RECENT_SEARCHES_KEY);
      setRecentSearchCount(0);
    } catch {
      // Ignore localStorage write failures.
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
        <p className="text-muted-foreground mt-1">
          Local dashboard preferences for this browser.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Appearance</CardTitle>
          <CardDescription>Set the UI theme preference.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="max-w-xs space-y-1">
            <label className="text-muted-foreground text-xs font-medium">
              Theme
            </label>
            <Select value={theme} onValueChange={handleThemeChange}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="system">System</SelectItem>
                <SelectItem value="light">Light</SelectItem>
                <SelectItem value="dark">Dark</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <p className="text-muted-foreground text-sm">
            Active theme: <span className="font-medium capitalize">{resolvedTheme}</span>
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Live Refresh Defaults</CardTitle>
          <CardDescription>
            Default behavior used by pages with live auto-refresh controls.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <AutoRefreshToggle
            enabled={autoRefreshControl.enabled}
            interval={autoRefreshControl.interval}
            onToggle={autoRefreshControl.setEnabled}
            onIntervalChange={autoRefreshControl.setInterval}
          />
          <p className="text-muted-foreground text-sm">
            This currently applies to Sessions and Timeline.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Command Palette</CardTitle>
          <CardDescription>Manage local quick-search history.</CardDescription>
        </CardHeader>
        <CardContent className="flex items-center justify-between gap-4">
          <p className="text-muted-foreground text-sm">
            Saved recent searches: <span className="font-medium">{recentSearchCount}</span>
          </p>
          <Button
            variant="outline"
            size="sm"
            disabled={recentSearchCount === 0}
            onClick={clearRecentSearches}
          >
            Clear recent searches
          </Button>
        </CardContent>
      </Card>

      <GoogleOAuthCard />
    </div>
  );
}
