import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  deleteGoogleCredentials,
  getGoogleCredentialStatus,
  getOAuthStatus,
  upsertGoogleCredentials,
} from "@/api/index.ts";
import type { UpsertAppCredentialsRequest } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export const secretsKeys = {
  all: ["secrets"] as const,
  credentialStatus: () => ["secrets", "credentials"] as const,
  oauthStatus: () => ["secrets", "oauth-status"] as const,
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/** Fetch masked credential presence status from the database. */
export function useGoogleCredentialStatus() {
  return useQuery({
    queryKey: secretsKeys.credentialStatus(),
    queryFn: () => getGoogleCredentialStatus(),
    retry: false,
  });
}

/** Fetch OAuth health status (probes Google token validity). */
export function useOAuthStatus() {
  return useQuery({
    queryKey: secretsKeys.oauthStatus(),
    queryFn: () => getOAuthStatus(),
    retry: false,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/** Store Google app credentials (client_id + client_secret). */
export function useUpsertGoogleCredentials() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: UpsertAppCredentialsRequest) => upsertGoogleCredentials(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: secretsKeys.all });
    },
  });
}

/** Delete all stored Google OAuth credentials. */
export function useDeleteGoogleCredentials() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => deleteGoogleCredentials(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: secretsKeys.all });
    },
  });
}

// ---------------------------------------------------------------------------
// Generic secrets CRUD hooks
// ---------------------------------------------------------------------------

import {
  deleteSecret,
  listSecrets,
  upsertSecret,
} from "@/api/index.ts";
import type { SecretUpsertRequest } from "@/api/index.ts";

export const genericSecretsKeys = {
  all: (butlerName: string) => ["secrets", "generic", butlerName] as const,
  list: (butlerName: string, category?: string) =>
    ["secrets", "generic", butlerName, "list", category ?? "all"] as const,
};

/** Fetch all secrets for a butler. */
export function useSecrets(butlerName: string, category?: string) {
  return useQuery({
    queryKey: genericSecretsKeys.list(butlerName, category),
    queryFn: () => listSecrets(butlerName, category),
    enabled: !!butlerName,
    retry: false,
  });
}

/** Create or update a secret for a butler. */
export function useUpsertSecret(butlerName: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ key, request }: { key: string; request: SecretUpsertRequest }) =>
      upsertSecret(butlerName, key, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: genericSecretsKeys.all(butlerName) });
    },
  });
}

/** Delete a secret from a butler's secret store. */
export function useDeleteSecret(butlerName: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => deleteSecret(butlerName, key),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: genericSecretsKeys.all(butlerName) });
    },
  });
}
