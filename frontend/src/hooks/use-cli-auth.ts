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
