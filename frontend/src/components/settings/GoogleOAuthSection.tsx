/**
 * GoogleOAuthSection — embeddable Google OAuth management section.
 *
 * Extracted from SettingsPage so it can be composed inside the IntegrationsCard.
 * Shows connected Google accounts with connect/disconnect/re-auth actions
 * and a per-account scope-set picker that replaces the read-only
 * ``granted_scopes`` CSV display.
 */

import { useState } from "react";

import type { GoogleAccount, OAuthCredentialState } from "@/api/index.ts";
import { getGoogleOAuthStartUrl, GOOGLE_HEALTH_SCOPES } from "@/api/index.ts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useDisconnectGoogleHealth } from "@/hooks/use-google-health";
import {
  useDisconnectAccount,
  useGoogleAccounts,
  useGoogleCredentialStatus,
  useSetPrimaryAccount,
} from "@/hooks/use-secrets";

// ---------------------------------------------------------------------------
// Badge helpers
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

function accountStatusBadge(
  status: GoogleAccount["status"],
): { variant: "default" | "secondary" | "destructive" | "outline"; label: string } {
  switch (status) {
    case "active":
      return { variant: "default", label: "Active" };
    case "revoked":
      return { variant: "destructive", label: "Revoked" };
    case "expired":
      return { variant: "destructive", label: "Expired" };
    default:
      return { variant: "secondary", label: status };
  }
}

function formatTimestamp(ts: string | null): string {
  if (!ts) return "\u2014";
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

// ---------------------------------------------------------------------------
// Disconnect dialog
// ---------------------------------------------------------------------------

function DisconnectAccountDialog({
  account,
  onDismiss,
}: {
  account: GoogleAccount;
  onDismiss: () => void;
}) {
  const [hardDelete, setHardDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const disconnectMutation = useDisconnectAccount();

  async function handleDisconnect() {
    setError(null);
    try {
      await disconnectMutation.mutateAsync({ accountId: account.id, hardDelete });
      onDismiss();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to disconnect account.");
    }
  }

  const displayName = account.email ?? account.display_name ?? account.id;

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Disconnect Google account?</DialogTitle>
        <DialogDescription>
          This will disconnect <strong>{displayName}</strong> from the butler.
          The OAuth token will be revoked.
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-3">
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={hardDelete}
            onChange={(e) => setHardDelete(e.target.checked)}
            className="rounded"
          />
          <span>Permanently delete account record (hard delete)</span>
        </label>
        <p className="text-xs text-muted-foreground">
          Without hard delete, the account row is retained with status &quot;disconnected&quot;.
          Hard delete removes the row and its companion entity entirely.
        </p>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <DialogFooter>
        <Button variant="outline" onClick={onDismiss}>
          Cancel
        </Button>
        <Button
          variant="destructive"
          onClick={handleDisconnect}
          disabled={disconnectMutation.isPending}
        >
          {disconnectMutation.isPending ? "Disconnecting..." : "Disconnect"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

// ---------------------------------------------------------------------------
// Scope-set picker
//
// Three rows, one per scope-set registered in ``GOOGLE_SCOPE_SETS`` on the
// backend (see ``src/butlers/api/routers/oauth.py``). Each row shows the
// current grant state derived by checking whether ``granted_scopes``
// contains *all* of that set's full scope URLs, and a toggle button that
// either starts the OAuth consent flow (grant) or, for Google Health,
// triggers the scope-selective disconnect endpoint (revoke).
//
// Revoking Calendar or Drive is intentionally NOT supported inline — those
// scope sets currently have no per-scope revocation endpoint, and the
// owner can still fully disconnect the account via the existing
// "Disconnect" button. The Calendar and Drive rows therefore render a
// disabled state when granted, with a small "via full disconnect" hint.
// ---------------------------------------------------------------------------

/** Calendar scope URLs — keep in lockstep with GOOGLE_SCOPE_SETS["calendar"]. */
const CALENDAR_SCOPES: readonly string[] = [
  "https://www.googleapis.com/auth/calendar",
];

/** Drive scope URLs — keep in lockstep with GOOGLE_SCOPE_SETS["drive"]. */
const DRIVE_SCOPES: readonly string[] = [
  "https://www.googleapis.com/auth/drive.readonly",
  "https://www.googleapis.com/auth/drive",
];

type ScopeSetKey = "calendar" | "drive" | "health";

interface ScopeSetDescriptor {
  key: ScopeSetKey;
  label: string;
  description: string;
  scopes: readonly string[];
  /**
   * Whether the scope set supports scope-selective revocation. Only
   * ``health`` has a backend endpoint for this today; the others fall
   * back to the full-account disconnect flow.
   */
  selectiveRevokeSupported: boolean;
  /** CTA copy for the ungranted state. */
  grantLabel: string;
  /** Confirm-modal copy when revoking a selectively-revocable scope set. */
  revokeConfirmCopy?: string;
  /** CTA shown on the ungranted row when it's the Google Health set. */
  grantHint?: string;
}

const SCOPE_SETS: ReadonlyArray<ScopeSetDescriptor> = [
  {
    key: "calendar",
    label: "Calendar",
    description:
      "Read, create, and update events on your Google Calendars.",
    scopes: CALENDAR_SCOPES,
    selectiveRevokeSupported: false,
    grantLabel: "Grant Calendar",
  },
  {
    key: "drive",
    label: "Drive",
    description:
      "Read and manage files in Google Drive (used by Drive-ingestion modules).",
    scopes: DRIVE_SCOPES,
    selectiveRevokeSupported: false,
    grantLabel: "Grant Drive",
  },
  {
    key: "health",
    label: "Google Health",
    description:
      "Ingest sleep, heart rate, HRV, SpO2, breathing rate, and activity for the Health butler.",
    scopes: GOOGLE_HEALTH_SCOPES,
    selectiveRevokeSupported: true,
    grantLabel: "Connect Google Health",
    revokeConfirmCopy:
      "This revokes Google Health access only. Calendar and Drive remain connected.",
    grantHint:
      "Connect Google Health to enable sleep, HR, HRV, and activity ingestion for the Health butler.",
  },
];

const ACCOUNT_REAUTHORIZE_SCOPE_SET = "calendar,drive,health";

function isScopeSetGranted(
  granted: string[] | null | undefined,
  required: readonly string[],
): boolean {
  if (!granted || granted.length === 0) return false;
  const set = new Set(granted);
  return required.every((scope) => set.has(scope));
}

function ScopeSetRevokeConfirmDialog({
  label,
  copy,
  onConfirm,
  onDismiss,
  pending,
  error,
}: {
  label: string;
  copy: string;
  onConfirm: () => void;
  onDismiss: () => void;
  pending: boolean;
  error: string | null;
}) {
  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Revoke {label} access?</DialogTitle>
        <DialogDescription>{copy}</DialogDescription>
      </DialogHeader>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <DialogFooter>
        <Button variant="outline" onClick={onDismiss} disabled={pending}>
          Cancel
        </Button>
        <Button variant="destructive" onClick={onConfirm} disabled={pending}>
          {pending ? "Revoking..." : `Revoke ${label}`}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

function ScopeSetPickerRow({
  descriptor,
  account,
}: {
  descriptor: ScopeSetDescriptor;
  account: GoogleAccount;
}) {
  const [revokeOpen, setRevokeOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const disconnectHealth = useDisconnectGoogleHealth();

  const granted = isScopeSetGranted(account.granted_scopes, descriptor.scopes);

  const grantUrl = getGoogleOAuthStartUrl({
    accountHint: account.email ?? undefined,
    forceConsent: true,
    scopeSet: descriptor.key,
  });

  async function handleConfirmRevoke() {
    setError(null);
    try {
      // Only Google Health has a scope-selective revocation endpoint;
      // the other sets' selectiveRevokeSupported flag is false so this
      // button is never rendered for them.
      if (descriptor.key === "health") {
        await disconnectHealth.mutateAsync();
      }
      setRevokeOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke scope set.");
    }
  }

  return (
    <div className="flex items-start justify-between gap-3 py-1.5">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium">{descriptor.label}</p>
          {granted ? (
            <Badge variant="secondary" className="text-xs">
              Granted
            </Badge>
          ) : (
            <Badge variant="outline" className="text-xs">
              Not granted
            </Badge>
          )}
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">
          {descriptor.description}
        </p>
        {!granted && descriptor.grantHint && (
          <p className="text-xs text-muted-foreground mt-0.5 italic">
            {descriptor.grantHint}
          </p>
        )}
      </div>
      <div className="shrink-0">
        {granted ? (
          descriptor.selectiveRevokeSupported ? (
            <Dialog open={revokeOpen} onOpenChange={setRevokeOpen}>
              <DialogTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive hover:text-destructive"
                  data-testid={`scope-set-revoke-${descriptor.key}`}
                >
                  Disconnect
                </Button>
              </DialogTrigger>
              <ScopeSetRevokeConfirmDialog
                label={descriptor.label}
                copy={descriptor.revokeConfirmCopy ?? ""}
                onConfirm={handleConfirmRevoke}
                onDismiss={() => setRevokeOpen(false)}
                pending={disconnectHealth.isPending}
                error={error}
              />
            </Dialog>
          ) : (
            <span
              className="text-xs text-muted-foreground"
              data-testid={`scope-set-revoke-${descriptor.key}`}
            >
              via full disconnect
            </span>
          )
        ) : (
          <a
            href={grantUrl}
            target="_blank"
            rel="noopener noreferrer"
            data-testid={`scope-set-grant-${descriptor.key}`}
          >
            <Button variant="outline" size="sm">
              {descriptor.grantLabel}
            </Button>
          </a>
        )}
      </div>
    </div>
  );
}

function ScopeSetPicker({ account }: { account: GoogleAccount }) {
  return (
    <div className="mt-2 rounded-md border border-border/60 bg-muted/20 p-3">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">
        Scope sets
      </p>
      <div className="divide-y divide-border/40">
        {SCOPE_SETS.map((descriptor) => (
          <ScopeSetPickerRow
            key={descriptor.key}
            descriptor={descriptor}
            account={account}
          />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single account row
// ---------------------------------------------------------------------------

function GoogleAccountRow({ account }: { account: GoogleAccount }) {
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const setPrimaryMutation = useSetPrimaryAccount();

  async function handleSetPrimary() {
    setError(null);
    try {
      await setPrimaryMutation.mutateAsync(account.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to set primary.");
    }
  }

  const { variant: statusVariant, label: statusLabel } = accountStatusBadge(account.status);
  const displayEmail = account.email ?? account.display_name ?? "Unknown account";
  const reAuthUrl = getGoogleOAuthStartUrl({
    accountHint: account.email ?? undefined,
    forceConsent: true,
    scopeSet: ACCOUNT_REAUTHORIZE_SCOPE_SET,
  });

  return (
    <div className="py-4 border-b border-border last:border-0 space-y-2">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-sm font-medium truncate">{displayEmail}</p>
            {account.display_name && account.email && (
              <span className="text-xs text-muted-foreground truncate">
                {account.display_name}
              </span>
            )}
            {account.is_primary && (
              <Badge variant="secondary" className="text-xs">Primary</Badge>
            )}
            <Badge variant={statusVariant}>{statusLabel}</Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">
            Connected: {formatTimestamp(account.connected_at)}
            {account.last_token_refresh_at && (
              <> &middot; Last refresh: {formatTimestamp(account.last_token_refresh_at)}</>
            )}
          </p>
          {/*
            Scope-set picker replaces the pre-existing read-only CSV display
            of granted_scopes. See "Per-Account Scope Set Picker" in
            openspec/changes/google-health-connector/specs/
            dashboard-google-accounts/spec.md.
          */}
          <ScopeSetPicker account={account} />
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!account.is_primary && account.status === "active" && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleSetPrimary}
              disabled={setPrimaryMutation.isPending}
            >
              {setPrimaryMutation.isPending ? "Setting..." : "Set primary"}
            </Button>
          )}
          <a href={reAuthUrl} target="_blank" rel="noopener noreferrer">
            <Button variant="outline" size="sm">
              Re-authorize
            </Button>
          </a>
          <Dialog open={disconnectOpen} onOpenChange={setDisconnectOpen}>
            <DialogTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="text-destructive hover:text-destructive"
              >
                Disconnect
              </Button>
            </DialogTrigger>
            <DisconnectAccountDialog
              account={account}
              onDismiss={() => setDisconnectOpen(false)}
            />
          </Dialog>
        </div>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// GoogleOAuthSection — embeddable variant (no Card wrapper)
// ---------------------------------------------------------------------------

export function GoogleOAuthSection() {
  const credStatusQuery = useGoogleCredentialStatus();
  const credStatus = credStatusQuery.data;
  const accountsQuery = useGoogleAccounts();
  const accounts = accountsQuery.data ?? [];
  const isLoading = credStatusQuery.isLoading || accountsQuery.isLoading;
  const isError = credStatusQuery.isError;
  const canStartOAuth =
    credStatus?.client_id_configured && credStatus?.client_secret_configured;

  const overallHealth = credStatus?.oauth_health ?? "not_configured";
  const connectUrl = getGoogleOAuthStartUrl({ forceConsent: true });

  if (isLoading) {
    return (
      <div>
        <h3 className="leading-none font-semibold">Google</h3>
        <Skeleton className="mt-3 h-12 w-full" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="leading-none font-semibold">Google</h3>
          <p className="text-muted-foreground text-sm mt-2">
            Manage connected Google accounts for Calendars, Emails, and Contacts.
          </p>
        </div>
        {isError ? (
          <Badge variant="destructive">Unavailable</Badge>
        ) : (
          <Badge variant={healthBadgeVariant(overallHealth)}>
            {healthBadgeLabel(overallHealth)}
          </Badge>
        )}
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium">Connected accounts</p>
          <a
            href={connectUrl}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => {
              if (!canStartOAuth) e.preventDefault();
            }}
          >
            <Button size="sm" disabled={!canStartOAuth || isLoading}>
              Connect new Google account
            </Button>
          </a>
        </div>
        {!canStartOAuth && !isLoading && (
          <p className="text-xs text-muted-foreground">
            Configure app credentials in{" "}
            <a href="/butlers/secrets" className="underline underline-offset-2">
              Secrets
            </a>{" "}
            before connecting accounts.
          </p>
        )}
        {isError ? (
          <p className="text-sm text-destructive">
            Failed to load account status. Ensure the dashboard API is running.
          </p>
        ) : accounts.length === 0 ? (
          <div className="rounded-md border border-dashed border-border p-6 text-center">
            <p className="text-sm text-muted-foreground">No Google accounts connected yet.</p>
            {canStartOAuth && (
              <p className="text-xs text-muted-foreground mt-1">
                Click &quot;Connect new Google account&quot; to start the OAuth flow.
              </p>
            )}
          </div>
        ) : (
          <div>
            {accounts.map((account) => (
              <GoogleAccountRow key={account.id} account={account} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
