/**
 * TanStack Query hooks for per-channel default ingestion policy
 * (public.channel_defaults, GET/PATCH /api/ingestion/channel-defaults/:channel).
 *
 * Separate from use-ingestion-rules.ts: channel defaults are a distinct
 * runtime policy document, not an IngestionRule row.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getChannelDefault, updateChannelDefault } from "@/api/index.ts";
import type { ChannelDefaultUpdate } from "@/api/index.ts";
import { ingestionRuleKeys } from "@/hooks/use-ingestion-rules";

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const channelDefaultKeys = {
  all: ["channel-defaults"] as const,
  detail: (channel: string) => [...channelDefaultKeys.all, channel] as const,
} as const;

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/**
 * Fetch a single channel's default policy. Disable until a channel is
 * actually being edited — the backend 404s for channels with no row yet,
 * which is an expected "not configured" state, not a fetch error.
 */
export function useChannelDefault(channel: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: channelDefaultKeys.detail(channel),
    queryFn: () => getChannelDefault(channel),
    enabled: (options?.enabled ?? true) && channel.length > 0,
    retry: false,
    staleTime: 30_000,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/** Upsert a channel's default policy. Invalidates that channel's cache on success. */
export function useUpdateChannelDefault() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ channel, body }: { channel: string; body: ChannelDefaultUpdate }) =>
      updateChannelDefault(channel, body),
    onSuccess: (_data, { channel }) => {
      queryClient.invalidateQueries({ queryKey: channelDefaultKeys.detail(channel) });
      queryClient.invalidateQueries({ queryKey: ingestionRuleKeys.all });
    },
  });
}
