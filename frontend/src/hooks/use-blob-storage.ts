/**
 * TanStack Query hooks for the blob storage settings API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getBlobStorageStatus,
  testBlobStorage,
  upsertSecret,
} from "@/api/index.ts";
import type { SecretUpsertRequest } from "@/api/index.ts";

const SHARED_TARGET = "shared";

export const blobStorageKeys = {
  status: ["blob-storage", "status"] as const,
};

/** Fetch current blob storage configuration status. */
export function useBlobStorageStatus() {
  return useQuery({
    queryKey: blobStorageKeys.status,
    queryFn: getBlobStorageStatus,
    retry: false,
  });
}

/** Test S3 connectivity. */
export function useTestBlobStorage() {
  return useMutation({
    mutationFn: testBlobStorage,
  });
}

/** Save a single blob storage secret and invalidate status. */
export function useSaveBlobSecret() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ key, value, isSensitive }: { key: string; value: string; isSensitive: boolean }) =>
      upsertSecret(SHARED_TARGET, key, {
        value,
        category: "storage",
        is_sensitive: isSensitive,
      } as SecretUpsertRequest),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: blobStorageKeys.status });
    },
  });
}
