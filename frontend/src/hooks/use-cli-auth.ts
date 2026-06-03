import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cancelCLIAuthSession,
  deleteCLIAuthApiKey,
  getCLIAuthSession,
  listCLIAuthProviders,
  saveCLIAuthApiKey,
  startCLIAuth,
  testCLIAuthApiKey,
} from "@/api/index.ts";
import { reauthorizeCliCredential } from "@/api/client.ts";
import type { CLIAuthSessionResponse } from "@/api/index.ts";
import { secretsInventoryKeys } from "@/hooks/use-secrets-inventory.ts";

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export const cliAuthKeys = {
  providers: () => ["cli-auth", "providers"] as const,
  session: (sessionId: string) => ["cli-auth", "session", sessionId] as const,
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/** List available CLI auth providers and their current status. */
export function useCLIAuthProviders() {
  return useQuery({
    queryKey: cliAuthKeys.providers(),
    queryFn: () => listCLIAuthProviders(),
    refetchInterval: 30_000,
  });
}

/** Poll a CLI auth session until it reaches a terminal state. */
export function useCLIAuthSession(sessionId: string | null) {
  return useQuery({
    queryKey: cliAuthKeys.session(sessionId ?? ""),
    queryFn: () => getCLIAuthSession(sessionId!),
    enabled: !!sessionId,
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      if (state === "success" || state === "failed" || state === "expired") {
        return false; // Stop polling
      }
      return 2_000; // Poll every 2s while in progress
    },
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/** Start a device-code auth flow for a provider. */
export function useStartCLIAuth() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (provider: string) => startCLIAuth(provider),
    onSuccess: () => {
      // Invalidate providers to refresh auth status after flow completes
      queryClient.invalidateQueries({ queryKey: cliAuthKeys.providers() });
    },
  });
}

/** Cancel a running CLI auth session. */
export function useCancelCLIAuth() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => cancelCLIAuthSession(sessionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cliAuthKeys.providers() });
    },
  });
}

/** Save an API key for an api_key-mode provider. */
export function useSaveCLIAuthApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ provider, apiKey }: { provider: string; apiKey: string }) =>
      saveCLIAuthApiKey(provider, apiKey),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cliAuthKeys.providers() });
    },
  });
}

/** Delete a stored API key for an api_key-mode provider. */
export function useDeleteCLIAuthApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (provider: string) => deleteCLIAuthApiKey(provider),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cliAuthKeys.providers() });
    },
  });
}

/** Test a stored API key by running the provider's test command. */
export function useTestCLIAuthApiKey() {
  return useMutation({
    mutationFn: (provider: string) => testCLIAuthApiKey(provider),
  });
}

// ---------------------------------------------------------------------------
// Device-code reauth flow (passport PageCli)
// ---------------------------------------------------------------------------

/**
 * Derive the CLI auth provider name from a secrets-inventory credential id.
 *
 * CLI runtime tokens are persisted to `butler_secrets` under the key
 * convention `cli-auth/{provider}` (see `butlers.cli_auth.persistence`), so the
 * inventory exposes them with id `cli-auth/codex`. The cli-auth endpoints key
 * on the bare provider name (`codex`), which is the segment after the slash.
 */
export function cliAuthProviderName(credentialId: string): string {
  const slash = credentialId.lastIndexOf("/");
  return slash >= 0 ? credentialId.slice(slash + 1) : credentialId;
}

/** State + actions for the PageCli device-code reauth flow. */
export interface CliDeviceAuthState {
  /** True when a matching provider exists and uses the device-code flow. */
  supported: boolean;
  /** True when the provider uses api_key auth mode (e.g. Claude). */
  isApiKeyMode: boolean;
  /** Bare provider name passed to the cli-auth endpoints. */
  providerName: string;
  /** Latest polled session, or null before the flow starts. */
  session: CLIAuthSessionResponse | null;
  /** A session has started and has not yet reached a terminal state. */
  inProgress: boolean;
  /** The start request is in flight. */
  starting: boolean;
  /**
   * The POST /api/secrets/cli/{id}/reauthorize request is in flight.
   * Distinct from `starting` so callers can show targeted feedback.
   */
  reauthorizing: boolean;
  /**
   * Set to true when POST /api/secrets/cli/{id}/reauthorize returns
   * auth_mode="api_key", signalling the caller to open the key-entry form.
   * Cleared when the caller calls `acknowledgeApiKeyReauth()`.
   */
  apiKeyReauthPending: boolean;
  /** Error from the start request, if any. */
  error: string | null;
  /** Begin a device-code flow for this provider (initial connect path). */
  start: () => void;
  /**
   * Re-authorize via POST /api/secrets/cli/{id}/reauthorize (audited path).
   * device_code response → drives the existing session-polling flow.
   * api_key response → sets apiKeyReauthPending so the caller opens the
   * key-entry panel.
   */
  reauthorize: () => void;
  /** Cancel the running session, if any. */
  cancel: () => void;
  /** Clear the apiKeyReauthPending flag once the caller has acted on it. */
  acknowledgeApiKeyReauth: () => void;
}

/**
 * Orchestrate the device-code reauth flow for a single CLI runtime credential.
 *
 * Looks the credential up against the live provider list to decide whether the
 * device-code flow applies, drives start/poll/cancel, and invalidates the
 * secrets inventory once the flow succeeds so the passport reflects the new
 * credential state.
 */
export function useCliDeviceAuth(credentialId: string): CliDeviceAuthState {
  const providerName = cliAuthProviderName(credentialId);
  const providersQuery = useCLIAuthProviders();
  const provider = providersQuery.data?.find((p) => p.name === providerName) ?? null;
  const supported = provider?.auth_mode === "device_code";
  const isApiKeyMode = provider?.auth_mode === "api_key";

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reauthorizing, setReauthorizing] = useState(false);
  const [apiKeyReauthPending, setApiKeyReauthPending] = useState(false);
  const startMutation = useStartCLIAuth();
  const cancelMutation = useCancelCLIAuth();
  const sessionQuery = useCLIAuthSession(sessionId);
  const session = sessionQuery.data ?? null;
  const queryClient = useQueryClient();

  const isTerminal =
    session?.state === "success" ||
    session?.state === "failed" ||
    session?.state === "expired";
  const inProgress = !!sessionId && !isTerminal;

  // On success, refresh the secrets inventory so the passport state updates.
  useEffect(() => {
    if (session?.state === "success") {
      queryClient.invalidateQueries({ queryKey: secretsInventoryKeys.all });
    }
  }, [session?.state, queryClient]);

  async function start() {
    setError(null);
    try {
      const result = await startMutation.mutateAsync(providerName);
      setSessionId(result.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start authentication.");
    }
  }

  /**
   * Re-authorize via POST /api/secrets/cli/{id}/reauthorize (audited path).
   *
   * device_code response: feeds the returned session_id into the existing
   * session-polling flow (useCLIAuthSession) without re-calling /start.
   *
   * api_key response: sets apiKeyReauthPending so PageCli can open the
   * key-entry panel; caller clears it via acknowledgeApiKeyReauth().
   */
  async function reauthorize() {
    setError(null);
    setReauthorizing(true);
    try {
      const result = await reauthorizeCliCredential(credentialId);
      const payload = result.data;
      if (payload.auth_mode === "device_code") {
        if (payload.session_id) {
          setSessionId(payload.session_id);
        }
      } else {
        // api_key branch — signal the caller to open the key-entry form
        setApiKeyReauthPending(true);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start re-authorization.");
    } finally {
      setReauthorizing(false);
    }
  }

  function cancel() {
    if (sessionId) {
      cancelMutation.mutate(sessionId);
      setSessionId(null);
    }
  }

  function acknowledgeApiKeyReauth() {
    setApiKeyReauthPending(false);
  }

  return {
    supported,
    isApiKeyMode,
    providerName,
    session,
    inProgress,
    starting: startMutation.isPending,
    reauthorizing,
    apiKeyReauthPending,
    error,
    start,
    reauthorize,
    cancel,
    acknowledgeApiKeyReauth,
  };
}
