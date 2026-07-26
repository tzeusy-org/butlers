/**
 * React Query hooks for the WhatsApp dashboard settings section.
 *
 * Provides hooks for status polling, QR pairing flow, session health
 * monitoring, and disconnect action.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  disconnectWhatsApp,
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

/**
 * Fast poll while the QR pairing modal is open (bu-ep4ks.15). No fleet-bus
 * event type covers WhatsApp pairing -- this cadence IS the update path.
 */
const WHATSAPP_PAIR_POLL_MS = 2_000;

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
 * Poll pairing progress while the QR modal is open.
 *
 * Polls every 2 seconds. Disable by passing `enabled: false` when the modal
 * is closed or when pairing has reached a terminal state.
 */
export function useWhatsAppPairPoll({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: whatsappKeys.pairPoll(),
    queryFn: () => pollWhatsAppPairing(),
    refetchInterval: enabled ? WHATSAPP_PAIR_POLL_MS : false,
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
