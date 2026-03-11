import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  deleteGoogleCredentials,
  deleteSecret,
  disconnectAccount,
  getAccountStatus,
  getGoogleAccounts,
  getGoogleCredentialStatus,
  getOAuthStatus,
  listSecrets,
  setPrimaryAccount,
  type ApiResponse,
  type SecretEntry,
  type SecretUpsertRequest,
  upsertGoogleCredentials,
  upsertSecret,
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

export const googleAccountsKeys = {
  all: ["google-accounts"] as const,
  list: () => ["google-accounts", "list"] as const,
  status: (accountId: string) => ["google-accounts", "status", accountId] as const,
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
// Multi-account Google hooks
// ---------------------------------------------------------------------------

/** Fetch all connected Google accounts. */
export function useGoogleAccounts() {
  return useQuery({
    queryKey: googleAccountsKeys.list(),
    queryFn: () => getGoogleAccounts(),
    retry: false,
  });
}

/** Fetch per-account credential status. */
export function useAccountStatus(accountId: string | null) {
  return useQuery({
    queryKey: googleAccountsKeys.status(accountId ?? ""),
    queryFn: () => getAccountStatus(accountId!),
    enabled: !!accountId,
    retry: false,
  });
}

/** Set a Google account as the primary account. */
export function useSetPrimaryAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (accountId: string) => setPrimaryAccount(accountId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: googleAccountsKeys.all });
      queryClient.invalidateQueries({ queryKey: secretsKeys.oauthStatus() });
    },
  });
}

/** Disconnect (or hard-delete) a Google account. */
export function useDisconnectAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ accountId, hardDelete }: { accountId: string; hardDelete?: boolean }) =>
      disconnectAccount(accountId, hardDelete),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: googleAccountsKeys.all });
      queryClient.invalidateQueries({ queryKey: secretsKeys.oauthStatus() });
    },
  });
}

// ---------------------------------------------------------------------------
// Generic secrets CRUD hooks
// ---------------------------------------------------------------------------

const SHARED_SECRETS_TARGET = "shared";

function normalizeSecretsTarget(value: string): string {
  return value.trim().toLowerCase();
}

export function isSharedSecretsTarget(value: string): boolean {
  return normalizeSecretsTarget(value) === SHARED_SECRETS_TARGET;
}

export function mergeResolvedSecrets(
  localSecrets: SecretEntry[],
  sharedSecrets: SecretEntry[],
): SecretEntry[] {
  const localKeys = new Set(localSecrets.map((secret) => secret.key.toUpperCase()));

  const inheritedShared = sharedSecrets
    .filter((secret) => !localKeys.has(secret.key.toUpperCase()))
    .map((secret) => ({ ...secret, source: "shared" }));

  return [...localSecrets, ...inheritedShared];
}

async function fetchSecretsForTarget(
  butlerName: string,
  category?: string,
): Promise<ApiResponse<SecretEntry[]>> {
  const localResponse = await listSecrets(butlerName, category);
  if (isSharedSecretsTarget(butlerName)) {
    return localResponse;
  }

  try {
    const sharedResponse = await listSecrets(SHARED_SECRETS_TARGET, category);
    return {
      ...localResponse,
      data: mergeResolvedSecrets(localResponse.data, sharedResponse.data),
    };
  } catch {
    // Shared DB availability should not break local-butler secret visibility.
    return localResponse;
  }
}

export const genericSecretsKeys = {
  all: (butlerName: string) => ["secrets", "generic", butlerName] as const,
  list: (butlerName: string, category?: string) =>
    ["secrets", "generic", butlerName, "list", category ?? "all"] as const,
};

/** Fetch all secrets for a butler. */
export function useSecrets(butlerName: string, category?: string) {
  return useQuery({
    queryKey: genericSecretsKeys.list(butlerName, category),
    queryFn: () => fetchSecretsForTarget(butlerName, category),
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
