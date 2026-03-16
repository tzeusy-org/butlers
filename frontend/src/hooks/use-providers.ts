/**
 * TanStack Query hooks for the provider configuration APIs.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createProvider,
  deleteProvider,
  listProviders,
  testProviderConnectivity,
  updateProvider,
} from "@/api/index.ts";
import type {
  ProviderConfigCreate,
  ProviderConfigUpdate,
} from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Provider CRUD
// ---------------------------------------------------------------------------

/** Fetch all configured providers. */
export function useProviders() {
  return useQuery({
    queryKey: ["providers"],
    queryFn: listProviders,
  });
}

/** Mutation to register a new provider. */
export function useCreateProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ProviderConfigCreate) => createProvider(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
  });
}

/** Mutation to update an existing provider. */
export function useUpdateProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      providerType,
      body,
    }: {
      providerType: string;
      body: ProviderConfigUpdate;
    }) => updateProvider(providerType, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
  });
}

/** Mutation to delete a provider. */
export function useDeleteProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (providerType: string) => deleteProvider(providerType),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
  });
}

/** Mutation to test connectivity for a provider. */
export function useTestProviderConnectivity() {
  return useMutation({
    mutationFn: (providerType: string) => testProviderConnectivity(providerType),
  });
}
