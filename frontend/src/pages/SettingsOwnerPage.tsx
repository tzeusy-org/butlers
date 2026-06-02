/**
 * SettingsOwnerPage — /settings/owner
 *
 * Owner-scoped configuration surface. Google OAuth app credentials are shared
 * application config, while account refresh tokens remain per Google account.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, KeyRound, RefreshCw, Save } from "lucide-react";
import { toast } from "sonner";

import {
  getGoogleCredentialStatus,
  getGoogleOAuthStartUrl,
  upsertGoogleCredentials,
} from "@/api/index.ts";
import type { GoogleCredentialStatusResponse } from "@/api/types.ts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

const googleStatusCopy: Record<GoogleCredentialStatusResponse["oauth_health"], string> = {
  connected: "Connected",
  not_configured: "Not configured",
  expired: "Expired",
  missing_scope: "Missing scope",
  redirect_uri_mismatch: "Redirect URI mismatch",
  unapproved_tester: "Tester not approved",
  unknown_error: "Needs attention",
};

function GoogleHealthBadge({ state }: { state: GoogleCredentialStatusResponse["oauth_health"] }) {
  if (state === "connected") {
    return <Badge>Connected</Badge>;
  }
  if (state === "not_configured") {
    return <Badge variant="outline">Not configured</Badge>;
  }
  return <Badge variant="destructive">{googleStatusCopy[state]}</Badge>;
}

function PresenceRow({
  label,
  present,
}: {
  label: string;
  present: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border/70 py-2 last:border-b-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <Badge variant={present ? "default" : "outline"}>{present ? "set" : "missing"}</Badge>
    </div>
  );
}

export default function SettingsOwnerPage() {
  const queryClient = useQueryClient();
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");

  const { data: googleStatus, isLoading, isError, refetch } = useQuery({
    queryKey: ["settings-owner", "google-oauth"],
    queryFn: getGoogleCredentialStatus,
    retry: false,
  });

  const oauthStartUrl = useMemo(
    () =>
      getGoogleOAuthStartUrl({
        forceConsent: true,
        pageOfOrigin: "settings_owner",
      }),
    [],
  );

  const saveMutation = useMutation({
    mutationFn: () =>
      upsertGoogleCredentials({
        client_id: clientId.trim(),
        client_secret: clientSecret.trim(),
      }),
    onSuccess: (response) => {
      setClientSecret("");
      void queryClient.invalidateQueries({ queryKey: ["settings-owner", "google-oauth"] });
      toast.success(response.message || "Google app credentials saved.");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Could not save Google credentials.");
    },
  });

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    saveMutation.mutate();
  }

  function handleReauthorize() {
    window.location.href = oauthStartUrl;
  }

  const canSave = clientId.trim().length > 0 && clientSecret.trim().length > 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-1">
        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          settings · owner
        </p>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Owner Config</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Identity and provider configuration for the primary operator.
            </p>
          </div>
          <Button variant="outline" asChild>
            <Link to="/secrets?focus=u:google">
              <ExternalLink className="h-4 w-4" />
              Credential passport
            </Link>
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <KeyRound className="h-4 w-4" />
              Google OAuth app
            </CardTitle>
            <CardDescription>
              Configure the OAuth client used by Gmail, Calendar, Contacts, Drive, and Google-backed modules.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={handleSubmit}>
              <div className="grid gap-2">
                <Label htmlFor="google-client-id">Google OAuth client ID</Label>
                <Input
                  id="google-client-id"
                  autoComplete="off"
                  placeholder="000000000000-example.apps.googleusercontent.com"
                  value={clientId}
                  onChange={(event) => setClientId(event.target.value)}
                  disabled={saveMutation.isPending}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="google-client-secret">Google OAuth client secret</Label>
                <Input
                  id="google-client-secret"
                  type="password"
                  autoComplete="new-password"
                  placeholder="Enter a new client secret"
                  value={clientSecret}
                  onChange={(event) => setClientSecret(event.target.value)}
                  disabled={saveMutation.isPending}
                />
                <p className="text-xs text-muted-foreground">
                  Stored values are write-only; save both fields when rotating app credentials.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button type="submit" disabled={!canSave || saveMutation.isPending}>
                  <Save className="h-4 w-4" />
                  {saveMutation.isPending ? "Saving..." : "Save app credentials"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={handleReauthorize}
                  disabled={isLoading}
                >
                  <RefreshCw className="h-4 w-4" />
                  Re-authorize Google
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Google status</CardTitle>
            <CardDescription>Presence and token health without exposing secret values.</CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="space-y-3">
                <Skeleton className="h-8 w-32" />
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : isError || !googleStatus ? (
              <div className="space-y-3">
                <p className="text-sm text-muted-foreground">Could not load Google OAuth status.</p>
                <Button variant="outline" size="sm" onClick={() => refetch()}>
                  Retry
                </Button>
              </div>
            ) : (
              <div className="space-y-4">
                <GoogleHealthBadge state={googleStatus.oauth_health} />
                <div className="rounded-md border border-border px-3">
                  <PresenceRow label="Client ID" present={googleStatus.client_id_configured} />
                  <PresenceRow label="Client secret" present={googleStatus.client_secret_configured} />
                  <PresenceRow label="Refresh token" present={googleStatus.refresh_token_present} />
                </div>
                {googleStatus.scope && (
                  <div>
                    <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                      granted scope
                    </p>
                    <p className="mt-1 break-words font-mono text-xs text-muted-foreground">
                      {googleStatus.scope}
                    </p>
                  </div>
                )}
                {googleStatus.oauth_health_remediation && (
                  <p className="text-sm text-muted-foreground">
                    {googleStatus.oauth_health_remediation}
                  </p>
                )}
                {googleStatus.oauth_health_detail && (
                  <p className="rounded-md bg-muted p-3 font-mono text-xs text-muted-foreground">
                    {googleStatus.oauth_health_detail}
                  </p>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
