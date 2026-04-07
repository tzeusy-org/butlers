/**
 * TanStack Query hooks for the butlers API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getButler,
  getButlerConfig,
  getButlerSkills,
  getButlers,
  getRuntimeConfig,
  patchRuntimeConfig,
} from "@/api/index.ts";
import type { RuntimeConfigPatch } from "@/api/index.ts";

/** Fetch all butlers with live status. */
export function useButlers() {
  return useQuery({
    queryKey: ["butlers"],
    queryFn: () => getButlers(),
    refetchInterval: 30_000,
  });
}

/** Fetch a single butler by name. */
export function useButler(name: string) {
  return useQuery({
    queryKey: ["butlers", name],
    queryFn: () => getButler(name),
    enabled: !!name,
  });
}

/** Fetch configuration files for a specific butler. */
export function useButlerConfig(name: string) {
  return useQuery({
    queryKey: ["butlers", name, "config"],
    queryFn: () => getButlerConfig(name),
    enabled: !!name,
  });
}

/** Fetch skills available to a specific butler. */
export function useButlerSkills(name: string) {
  return useQuery({
    queryKey: ["butlers", name, "skills"],
    queryFn: () => getButlerSkills(name),
    enabled: !!name,
  });
}

/** Fetch runtime config for a butler from the DB-backed runtime_config table. */
export function useRuntimeConfig(name: string) {
  return useQuery({
    queryKey: ["butlers", name, "runtime-config"],
    queryFn: () => getRuntimeConfig(name),
    enabled: !!name,
  });
}

/** Mutation hook for patching runtime config. */
export function usePatchRuntimeConfig(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (patch: RuntimeConfigPatch) => patchRuntimeConfig(name, patch),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["butlers", name, "runtime-config"],
      });
    },
  });
}
