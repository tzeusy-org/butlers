/**
 * React Query hooks for the WhatsApp dashboard settings section.
 *
 * Provides hooks for status polling, QR pairing flow, session health
 * monitoring, and disconnect action.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  disconnectWhatsApp,
  getWhatsAppHealth,
  getWhatsAppStatus,
  pollWhatsAppPairing,
  startWhatsAppPairing,
} from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export const whatsappKeys = {
  all: ["whatsapp"] as const,
  status: () => ["whatsapp", "status"] as const,
  health: () => ["whatsapp", "health"] as const,
  pairPoll: () => ["whatsapp", "pair-poll"] as const,
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/**
 * Fetch the current WhatsApp connection state.
 *
 * Suitable for on-mount display; does not auto-poll by default.
 * Invalidate via `whatsappKeys.status()` after pairing or disconnect.
 */
export function useWhatsAppStatus() {
  return useQuery({
    queryKey: whatsappKeys.status(),
    queryFn: () => getWhatsAppStatus(),
    retry: false,
  });
}

/**
 * Fetch WhatsApp session health, auto-polling every 30 seconds.
 *
 * Used to keep the health badge current while the settings page is open.
 * Only active when `enabled` is true (default); pass `enabled: false` when
 * the user is in the middle of a pairing flow to reduce noise.
 */
export function useWhatsAppHealth({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: whatsappKeys.health(),
    queryFn: () => getWhatsAppHealth(),
    refetchInterval: enabled ? 30_000 : false,
    retry: false,
  });
}

/**
 * Poll pairing progress while the QR modal is open.
 *
 * Polls every 2 seconds. Disable by passing `enabled: false` when the modal
 * is closed or when pairing has reached a terminal state.
 */
export function useWhatsAppPairPoll({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: whatsappKeys.pairPoll(),
    queryFn: () => pollWhatsAppPairing(),
    refetchInterval: enabled ? 2_000 : false,
    retry: false,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/**
 * Initiate the QR pairing flow.
 *
 * Calls POST /api/connectors/whatsapp/pair/start and returns the QR data URI.
 * Query invalidation (status/health) is handled by the caller via the
 * `onPaired` callback once pairing is confirmed, not by this mutation hook.
 */
export function useWhatsAppPairStart() {
  return useMutation({
    mutationFn: () => startWhatsAppPairing(),
  });
}

/**
 * Disconnect the WhatsApp account.
 *
 * On success, invalidates both status and health queries so the settings card
 * transitions immediately to the not_configured state.
 */
export function useWhatsAppDisconnect() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => disconnectWhatsApp(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: whatsappKeys.all });
    },
  });
}
