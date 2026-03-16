/**
 * TanStack Query hooks for the provider configuration and Ollama discovery APIs.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createProvider,
  deleteProvider,
  discoverOllamaModels,
  importOllamaModels,
  listProviders,
  testProviderConnectivity,
  updateProvider,
} from "@/api/index.ts";
import type {
  OllamaImportRequest,
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

// ---------------------------------------------------------------------------
// Ollama discovery + import
// ---------------------------------------------------------------------------

/** Mutation to discover models from an Ollama provider. */
export function useDiscoverOllamaModels() {
  return useMutation({
    mutationFn: () => discoverOllamaModels(),
  });
}

/** Mutation to import discovered Ollama models into the catalog. */
export function useImportOllamaModels() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: OllamaImportRequest) => importOllamaModels(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["model-catalog"] });
    },
  });
}
