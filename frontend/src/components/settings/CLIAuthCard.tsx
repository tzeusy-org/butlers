import { useEffect, useState } from "react";

import type {
  CLIAuthHealthState,
  CLIAuthProvider,
  CLIAuthSessionState,
} from "@/api/index.ts";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  useCancelCLIAuth,
  useCLIAuthProviders,
  useCLIAuthSession,
  useDeleteCLIAuthApiKey,
  useSaveCLIAuthApiKey,
  useStartCLIAuth,
  useTestCLIAuthApiKey,
} from "@/hooks/use-cli-auth";

function cliHealthBadge(
  health: CLIAuthHealthState | null,
  authenticated: boolean,
): { variant: "default" | "outline" | "destructive" | "secondary"; label: string } {
  if (health === "authenticated") return { variant: "default", label: "Connected" };
  if (health === "not_authenticated") return { variant: "destructive", label: "Not authenticated" };
  if (health === "probe_failed") return { variant: "secondary", label: "Probe failed" };
  return authenticated
    ? { variant: "default", label: "Token present" }
    : { variant: "outline", label: "Not authenticated" };
}

function sessionStateBadge(
  state: CLIAuthSessionState,
): { variant: "default" | "secondary" | "destructive" | "outline"; label: string } {
  switch (state) {
    case "starting":
      return { variant: "secondary", label: "Starting..." };
    case "awaiting_auth":
      return { variant: "outline", label: "Waiting for authorization" };
    case "success":
      return { variant: "default", label: "Connected" };
    case "failed":
      return { variant: "destructive", label: "Failed" };
    case "expired":
      return { variant: "destructive", label: "Expired" };
    default:
      return { variant: "secondary", label: state };
  }
}

function CLIAuthDeviceCodeRow({ provider }: { provider: CLIAuthProvider }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const startMutation = useStartCLIAuth();
  const cancelMutation = useCancelCLIAuth();
  const sessionQuery = useCLIAuthSession(sessionId);
  const session = sessionQuery.data;

  const isTerminal =
    session?.state === "success" ||
    session?.state === "failed" ||
    session?.state === "expired";

  const { refetch: refetchProviders } = useCLIAuthProviders();
  useEffect(() => {
    if (session?.state === "success") {
      refetchProviders();
    }
  }, [session?.state, refetchProviders]);

  async function handleStart() {
    try {
      const result = await startMutation.mutateAsync(provider.name);
      setSessionId(result.session_id);
    } catch {
      // Error surfaced via mutation state below.
    }
  }

  function handleCancel() {
    if (sessionId) {
      cancelMutation.mutate(sessionId);
      setSessionId(null);
    }
  }

  const isInProgress = sessionId && !isTerminal;
  const healthBadge = cliHealthBadge(provider.health, provider.authenticated);

  return (
    <div className="space-y-3 py-4 border-b border-border last:border-0">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium">{provider.display_name}</p>
          {provider.token_path && (
            <p className="text-xs text-muted-foreground font-mono">
              {provider.token_path}
            </p>
          )}
        </div>
        <Badge variant={healthBadge.variant}>{healthBadge.label}</Badge>
      </div>

      {provider.health_detail && provider.health !== "authenticated" && (
        <p className="text-xs text-muted-foreground">{provider.health_detail}</p>
      )}

      {session && sessionId && (
        <div className="rounded-md bg-muted/50 p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Badge variant={sessionStateBadge(session.state).variant}>
              {sessionStateBadge(session.state).label}
            </Badge>
            {session.message && session.state !== "awaiting_auth" && (
              <span className="text-sm text-muted-foreground">{session.message}</span>
            )}
          </div>

          {session.state === "awaiting_auth" && session.auth_url && session.device_code && (
            <div className="space-y-2">
              <p className="text-sm">
                Open{" "}
                <a
                  href={session.auth_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium underline underline-offset-2"
                >
                  {session.auth_url}
                </a>{" "}
                and enter the code:
              </p>
              <div className="flex items-center gap-3">
                <code className="text-2xl font-bold tracking-widest bg-background px-4 py-2 rounded border">
                  {session.device_code}
                </code>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => navigator.clipboard.writeText(session.device_code!)}
                >
                  Copy
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="flex items-center gap-2">
        {isInProgress ? (
          <Button variant="outline" size="sm" onClick={handleCancel}>
            Cancel
          </Button>
        ) : (
          <Button
            size="sm"
            variant={provider.authenticated ? "outline" : "default"}
            onClick={handleStart}
            disabled={startMutation.isPending}
          >
            {startMutation.isPending
              ? "Starting..."
              : provider.authenticated
                ? "Re-authenticate"
                : "Login"}
          </Button>
        )}
      </div>
    </div>
  );
}

function CLIAuthApiKeyRow({ provider }: { provider: CLIAuthProvider }) {
  const [apiKey, setApiKey] = useState("");
  const [saved, setSaved] = useState(false);
  const saveMutation = useSaveCLIAuthApiKey();
  const deleteMutation = useDeleteCLIAuthApiKey();
  const testMutation = useTestCLIAuthApiKey();

  async function handleSave() {
    setSaved(false);
    await saveMutation.mutateAsync({ provider: provider.name, apiKey: apiKey.trim() });
    setApiKey("");
    setSaved(true);
  }

  function handleTest() {
    testMutation.mutate(provider.name);
  }

  async function handleDelete() {
    setSaved(false);
    await deleteMutation.mutateAsync(provider.name);
  }

  const healthBadge = cliHealthBadge(provider.health, provider.authenticated);

  return (
    <div className="space-y-3 py-4 border-b border-border last:border-0">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium">{provider.display_name}</p>
          {provider.env_var && (
            <p className="text-xs text-muted-foreground font-mono">
              {provider.env_var}
            </p>
          )}
        </div>
        <Badge variant={healthBadge.variant}>{healthBadge.label}</Badge>
      </div>

      {provider.health_detail && provider.health !== "authenticated" && (
        <p className="text-xs text-muted-foreground">{provider.health_detail}</p>
      )}

      <div className="flex items-center gap-2">
        <Input
          type="password"
          placeholder="Enter API key"
          value={apiKey}
          onChange={(e) => {
            setApiKey(e.target.value);
            setSaved(false);
          }}
          autoComplete="new-password"
          className="max-w-sm h-8 text-sm"
        />
        <Button
          size="sm"
          onClick={handleSave}
          disabled={saveMutation.isPending || !apiKey.trim()}
        >
          {saveMutation.isPending ? "Saving..." : "Save"}
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={handleTest}
          disabled={testMutation.isPending || !provider.authenticated}
        >
          {testMutation.isPending ? "Testing..." : "Test"}
        </Button>
        {provider.authenticated && (
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={handleDelete}
            disabled={deleteMutation.isPending}
          >
            Delete
          </Button>
        )}
      </div>

      {saved && !saveMutation.isError && (
        <p className="text-sm text-green-600 dark:text-green-400">
          API key saved successfully.
        </p>
      )}
      {saveMutation.isError && (
        <p className="text-sm text-destructive">
          Failed to save:{" "}
          {saveMutation.error instanceof Error
            ? saveMutation.error.message
            : "Unknown error"}
        </p>
      )}
      {testMutation.data && (
        <p
          className={`text-sm ${
            testMutation.data.success
              ? "text-green-600 dark:text-green-400"
              : "text-destructive"
          }`}
        >
          {testMutation.data.success ? "Test passed" : "Test failed"}
          {testMutation.data.detail && `: ${testMutation.data.detail}`}
        </p>
      )}
      {testMutation.isError && (
        <p className="text-sm text-destructive">
          Test failed:{" "}
          {testMutation.error instanceof Error
            ? testMutation.error.message
            : "Unknown error"}
        </p>
      )}
    </div>
  );
}

export function CLIAuthCard() {
  const { data: providers, isLoading, isError } = useCLIAuthProviders();

  return (
    <Card id="cli-auth-card">
      <CardHeader>
        <CardTitle>CLI runtime authentication</CardTitle>
        <CardDescription>
          Authenticate CLI tools used by butler runtimes via device-code flow
          (Codex, OpenCode) or API key (Claude, etc.).
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading && (
          <div className="space-y-2">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        )}
        {isError && (
          <p className="text-sm text-destructive">
            Failed to load CLI auth providers. Ensure the dashboard API is running.
          </p>
        )}
        {!isLoading && !isError && providers && providers.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No CLI tools found on the server. Install opencode or codex to enable
            device-code authentication.
          </p>
        )}
        {!isLoading && !isError &&
          providers?.map((p) =>
            p.auth_mode === "api_key" ? (
              <CLIAuthApiKeyRow key={p.name} provider={p} />
            ) : (
              <CLIAuthDeviceCodeRow key={p.name} provider={p} />
            ),
          )}
      </CardContent>
    </Card>
  );
}
