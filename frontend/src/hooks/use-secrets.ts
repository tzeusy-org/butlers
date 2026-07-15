import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  disconnectAccount,
  getAccountStatus,
  getGoogleAccounts,
  setPrimaryAccount,
  type SecretEntry,
} from "@/api/index.ts";

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

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

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

/** Batch-fetch per-account token health for all Google accounts. */
/** @public knip mis-traces this import (live consumer exists); remove tag when bu-9jvhm fixes the tracing gap. */
export function useGoogleAccountsHealth(accountIds: string[]) {
  return useQueries({
    queries: accountIds.map((id) => ({
      queryKey: googleAccountsKeys.status(id),
      queryFn: () => getAccountStatus(id),
      retry: false,
    })),
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
