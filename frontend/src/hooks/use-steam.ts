/**
 * React Query hooks for the Steam dashboard settings section.
 *
 * Provides hooks for listing accounts, connecting/disconnecting accounts,
 * and fetching playtime analytics.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  connectSteamAccount,
  disconnectSteamAccount,
  getSteamPlaytime,
  listSteamAccounts,
} from "@/api/index.ts";
import type { SteamConnectRequest } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export const steamKeys = {
  all: ["steam"] as const,
  accounts: () => ["steam", "accounts"] as const,
  playtime: (accountId?: string) => ["steam", "playtime", accountId ?? "primary"] as const,
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/**
 * Fetch the list of connected Steam accounts.
 *
 * Suitable for on-mount display; does not auto-poll.
 * Invalidate via `steamKeys.accounts()` after connect or disconnect.
 */
export function useSteamAccounts() {
  return useQuery({
    queryKey: steamKeys.accounts(),
    queryFn: () => listSteamAccounts(),
    retry: false,
  });
}

/**
 * Fetch playtime analytics for a Steam account.
 *
 * If `accountId` is omitted, uses the primary account.
 * Only runs when at least one account is present (enabled flag).
 */
export function useSteamPlaytime(accountId?: string, enabled = true) {
  return useQuery({
    queryKey: steamKeys.playtime(accountId),
    queryFn: () => getSteamPlaytime({ account_id: accountId, top_n: 10 }),
    enabled,
    retry: false,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/**
 * Connect a new Steam account.
 *
 * On success, invalidates the accounts query so the list refreshes.
 */
export function useSteamConnect() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: SteamConnectRequest) => connectSteamAccount(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: steamKeys.all });
    },
  });
}

/**
 * Disconnect (soft-revoke) a Steam account.
 *
 * On success, invalidates all Steam queries so the UI reflects the removal.
 */
export function useSteamDisconnect() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (accountId: string) => disconnectSteamAccount(accountId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: steamKeys.all });
    },
  });
}
