import { useState } from "react";

import type { OAuthCredentialState, SecretEntry } from "@/api/index.ts";
import { getOAuthStartUrl } from "@/api/index.ts";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { SecretFormModal } from "@/components/secrets/SecretFormModal";
import { SecretsTable } from "@/components/secrets/SecretsTable";
import { useButlers } from "@/hooks/use-butlers";
import {
  useDeleteGoogleCredentials,
  useGoogleCredentialStatus,
  useSecrets,
  useUpsertGoogleCredentials,
} from "@/hooks/use-secrets";

export const SHARED_SECRETS_TARGET = "shared";

export function buildSecretsTargets(butlerNames: string[]): string[] {
  const sharedTarget = SHARED_SECRETS_TARGET.toLowerCase();
  const nonSharedButlers = butlerNames.filter(
    (name) => name.trim().toLowerCase() !== sharedTarget,
  );
  return [SHARED_SECRETS_TARGET, ...nonSharedButlers];
}

function formatSecretsTargetLabel(target: string): string {
  if (target.trim().toLowerCase() === SHARED_SECRETS_TARGET) {
    return SHARED_SECRETS_TARGET;
  }
  return target;
}

// ---------------------------------------------------------------------------
// Health badge helper
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

// ---------------------------------------------------------------------------
// Presence indicator row
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Credential form
// ---------------------------------------------------------------------------

function CredentialForm() {
  const upsertMutation = useUpsertGoogleCredentials();
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(false);

    try {
      await upsertMutation.mutateAsync({ client_id: clientId, client_secret: clientSecret });
      setSuccess(true);
      setClientId("");
      setClientSecret("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save credentials.");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="space-y-1.5">
        <Label htmlFor="client-id">Client ID</Label>
        <Input
          id="client-id"
          type="text"
          placeholder="xxxxxxxx.apps.googleusercontent.com"
          value={clientId}
          onChange={(e) => setClientId(e.target.value)}
          autoComplete="off"
          required
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="client-secret">Client Secret</Label>
        <Input
          id="client-secret"
          type="password"
          placeholder="GOCSPX-..."
          value={clientSecret}
          onChange={(e) => setClientSecret(e.target.value)}
          autoComplete="new-password"
          required
        />
        <p className="text-xs text-muted-foreground">
          Secret values are stored in the database and never echoed back.
        </p>
      </div>
      {error && (
        <p className="text-sm text-destructive">{error}</p>
      )}
      {success && (
        <p className="text-sm text-green-600 dark:text-green-400">
          Credentials saved successfully.
        </p>
      )}
      <Button
        type="submit"
        disabled={upsertMutation.isPending || !clientId.trim() || !clientSecret.trim()}
      >
        {upsertMutation.isPending ? "Saving..." : "Save credentials"}
      </Button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Delete confirmation dialog
// ---------------------------------------------------------------------------

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
// Google OAuth section
// ---------------------------------------------------------------------------

function GoogleOAuthSection() {
  const credStatusQuery = useGoogleCredentialStatus();
  const credStatus = credStatusQuery.data;
  const isLoading = credStatusQuery.isLoading;
  const isError = credStatusQuery.isError;
  const oauthStartUrl = getOAuthStartUrl();
  const canStartOAuth =
    credStatus?.client_id_configured && credStatus?.client_secret_configured;

  return (
    <>
      {/* Credential status card */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Google OAuth Credentials</CardTitle>
              <CardDescription>
                Credential presence and OAuth health status.
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
        <CardContent className="space-y-2">
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
        </CardContent>
      </Card>

      {/* Connect Google button */}
      <Card>
        <CardHeader>
          <CardTitle>Connect Google</CardTitle>
          <CardDescription>
            Trigger the OAuth authorization flow to obtain a refresh token.
            Requires client_id and client_secret to be configured first.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex items-center gap-4">
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
              Configure client_id and client_secret below before connecting.
            </p>
          )}
          {credStatus?.oauth_health === "connected" && (
            <p className="text-sm text-green-600 dark:text-green-400">
              Google account is connected and credentials are valid.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Add/update credentials form */}
      <Card>
        <CardHeader>
          <CardTitle>Configure App Credentials</CardTitle>
          <CardDescription>
            Enter your Google Cloud OAuth client credentials.
            These are stored in the database — not env vars.
            An existing refresh token is preserved when updating app credentials.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <CredentialForm />
        </CardContent>
      </Card>

      {/* Danger zone: delete */}
      <Card className="border-destructive/50">
        <CardHeader>
          <CardTitle className="text-destructive">Danger Zone</CardTitle>
          <CardDescription>
            Delete all stored Google OAuth credentials. This cannot be undone.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <DeleteCredentialsDialog />
        </CardContent>
      </Card>
    </>
  );
}

// ---------------------------------------------------------------------------
// Generic secrets section (shared + per-butler)
// ---------------------------------------------------------------------------

function GenericSecretsSection() {
  interface SecretPrefill {
    key: string;
    category: string;
    description: string | null;
  }

  const { data: butlersResponse, isLoading: butlersLoading } = useButlers();
  const butlerNames = butlersResponse?.data?.map((b) => b.name) ?? [];
  const secretTargets = buildSecretsTargets(butlerNames);

  const [selectedTarget, setSelectedTarget] = useState<string>("");
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [addPrefill, setAddPrefill] = useState<SecretPrefill | null>(null);
  const [editSecret, setEditSecret] = useState<SecretEntry | null>(null);

  // Pick first available target by default once loaded.
  const activeTarget = selectedTarget || (secretTargets[0] ?? "");

  const { data: secretsResponse, isLoading, isError } = useSecrets(activeTarget);
  const secrets = secretsResponse?.data ?? [];

  function handleEdit(secret: SecretEntry) {
    setEditSecret(secret);
  }

  function handleCreateOverride(prefill: SecretPrefill) {
    setAddPrefill(prefill);
    setAddModalOpen(true);
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle>Secrets</CardTitle>
            <CardDescription>
              Known secret requirements plus resolved values, grouped by category.
              Manage shared defaults and local per-butler overrides.
            </CardDescription>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {/* Butler selector */}
            {butlersLoading ? (
              <Skeleton className="h-9 w-36" />
            ) : secretTargets.length > 1 ? (
              <Select
                value={activeTarget}
                onValueChange={setSelectedTarget}
              >
                <SelectTrigger className="w-36">
                  <SelectValue placeholder="Select target" />
                </SelectTrigger>
                <SelectContent>
                  {secretTargets.map((name) => (
                    <SelectItem key={name} value={name}>
                      {formatSecretsTargetLabel(name)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : null}
            <Button
              size="sm"
              onClick={() => {
                setAddPrefill(null);
                setAddModalOpen(true);
              }}
              disabled={!activeTarget}
            >
              Add Secret
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {!activeTarget ? (
          <p className="text-sm text-muted-foreground">
            No secret target available. Check dashboard DB configuration.
          </p>
        ) : (
          <SecretsTable
            butlerName={activeTarget}
            secrets={secrets}
            isLoading={isLoading}
            isError={isError}
            onEdit={handleEdit}
            onCreateOverride={handleCreateOverride}
          />
        )}
      </CardContent>

      {/* Add modal */}
      <SecretFormModal
        butlerName={activeTarget}
        prefill={addPrefill}
        open={addModalOpen}
        onOpenChange={(open) => {
          setAddModalOpen(open);
          if (!open) setAddPrefill(null);
        }}
      />

      {/* Edit modal */}
      <SecretFormModal
        butlerName={activeTarget}
        editSecret={editSecret}
        open={!!editSecret}
        onOpenChange={(open) => {
          if (!open) setEditSecret(null);
        }}
      />
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function SecretsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Secrets</h1>
        <p className="text-muted-foreground mt-1">
          Manage secrets and OAuth credentials stored in the database.
          Suggested keys, inherited sources, and local overrides are shown without exposing values.
        </p>
      </div>

      {/* Generic secrets management — main section */}
      <GenericSecretsSection />

      {/* Google OAuth — specialized section */}
      <div className="space-y-4">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Google OAuth</h2>
          <p className="text-muted-foreground text-sm mt-0.5">
            Configure the Google OAuth app credentials and authorization flow.
          </p>
        </div>
        <GoogleOAuthSection />
      </div>
    </div>
  );
}
