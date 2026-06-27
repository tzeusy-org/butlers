/**
 * TanStack Query mutation hooks for the secrets passport page (bu-ayp6v.2).
 *
 * Each hook wraps exactly one API client function from the Secrets v2 layer,
 * invalidates the secrets inventory + relevant per-credential query key on
 * success, and surfaces success/error toasts via sonner.
 *
 * Scope: hooks layer only — no button wiring (that lives in .3/.4/.5).
 *
 * Query key namespaces:
 *   secretsUserKeys   — per-user-credential evidence
 *   secretsSystemKeys — per-system-credential evidence
 *   secretsCliKeys    — per-CLI-credential evidence
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  createEntityInfo,
  deleteSystemCredential,
  disconnectUserCredential,
  probeSystemCredential,
  probeUserCredential,
  rotateCliCredential,
  rotateUserCredential,
  revokeCliCredential,
  setSystemCredential,
} from "@/api/client.ts";
import type {
  CreateEntityInfoRequest,
  SecretsRotateUserRequest,
  SecretsSystemSetRequest,
} from "@/api/types.ts";
import { secretsInventoryKeys } from "@/hooks/use-secrets-inventory.ts";

// ---------------------------------------------------------------------------
// Per-credential query keys
//
// These mirror the inventory key namespace but are scoped to individual
// credential evidence fetches (GET /api/secrets/user/<provider>, etc.).
// They are defined here so mutation hooks can target precise invalidations.
// ---------------------------------------------------------------------------

export const secretsUserKeys = {
  all: ["secrets", "user"] as const,
  byProvider: (provider: string, identity?: string | null) =>
    ["secrets", "user", provider, identity ?? "owner"] as const,
};

export const secretsSystemKeys = {
  all: ["secrets", "system"] as const,
  byKey: (key: string) => ["secrets", "system", key] as const,
};

export const secretsCliKeys = {
  all: ["secrets", "cli"] as const,
  byId: (id: string) => ["secrets", "cli", id] as const,
};

// ---------------------------------------------------------------------------
// User credential mutations
// ---------------------------------------------------------------------------

/** Rotate (replace) a stored user-scoped credential. */
export function useRotateUserSecret() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      provider,
      body,
      identity,
    }: {
      provider: string;
      body: SecretsRotateUserRequest;
      identity?: string;
    }) => rotateUserCredential(provider, body, identity),
    onSuccess: (_, { provider, identity }) => {
      toast.success("Credential rotated");
      void queryClient.invalidateQueries({ queryKey: secretsInventoryKeys.all });
      void queryClient.invalidateQueries({
        queryKey: secretsUserKeys.byProvider(provider, identity),
      });
    },
    onError: (error: Error) => {
      toast.error(`Rotate failed: ${error.message}`);
    },
  });
}

/** Disconnect (hard-delete) a user-scoped credential. */
export function useDisconnectUserSecret() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      provider,
      identity,
    }: {
      provider: string;
      identity?: string;
    }) => disconnectUserCredential(provider, identity),
    onSuccess: (_, { provider, identity }) => {
      toast.success("Credential disconnected");
      void queryClient.invalidateQueries({ queryKey: secretsInventoryKeys.all });
      void queryClient.invalidateQueries({
        queryKey: secretsUserKeys.byProvider(provider, identity),
      });
    },
    onError: (error: Error) => {
      toast.error(`Disconnect failed: ${error.message}`);
    },
  });
}

/** Probe (live-verify) a user-scoped credential. */
export function useProbeUserSecret() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      provider,
      identity,
    }: {
      provider: string;
      identity?: string;
    }) => probeUserCredential(provider, identity),
    onSuccess: (data, { provider, identity }) => {
      const ok = data?.data?.ok;
      if (ok) {
        toast.success("Probe passed");
      } else {
        toast.error(`Probe failed: ${data?.data?.message ?? "no details"}`);
      }
      void queryClient.invalidateQueries({ queryKey: secretsInventoryKeys.all });
      void queryClient.invalidateQueries({
        queryKey: secretsUserKeys.byProvider(provider, identity),
      });
    },
    onError: (error: Error) => {
      toast.error(`Probe failed: ${error.message}`);
    },
  });
}

// ---------------------------------------------------------------------------
// System credential mutations
// ---------------------------------------------------------------------------

/** Set (create, rotate, or override) a system credential. */
export function useSetSystemSecret() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      key,
      body,
    }: {
      key: string;
      body: SecretsSystemSetRequest;
    }) => setSystemCredential(key, body),
    onSuccess: (_, { key }) => {
      toast.success("Credential saved");
      void queryClient.invalidateQueries({ queryKey: secretsInventoryKeys.all });
      void queryClient.invalidateQueries({ queryKey: secretsSystemKeys.byKey(key) });
    },
    onError: (error: Error) => {
      toast.error(`Save failed: ${error.message}`);
    },
  });
}

/** Probe (verify state of) a system credential. */
export function useProbeSystemSecret() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ key }: { key: string }) => probeSystemCredential(key),
    onSuccess: (data, { key }) => {
      const ok = data?.data?.ok;
      if (ok) {
        toast.success("Probe passed");
      } else {
        toast.error(`Probe failed: ${data?.data?.message ?? "no details"}`);
      }
      void queryClient.invalidateQueries({ queryKey: secretsInventoryKeys.all });
      void queryClient.invalidateQueries({ queryKey: secretsSystemKeys.byKey(key) });
    },
    onError: (error: Error) => {
      toast.error(`Probe failed: ${error.message}`);
    },
  });
}

/** Delete a system credential (shared row or per-butler override). */
export function useDeleteSystemSecret() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      key,
      target,
    }: {
      key: string;
      target?: "shared" | string;
    }) => deleteSystemCredential(key, target),
    onSuccess: (_, { key }) => {
      toast.success("Credential deleted");
      void queryClient.invalidateQueries({ queryKey: secretsInventoryKeys.all });
      void queryClient.invalidateQueries({ queryKey: secretsSystemKeys.byKey(key) });
    },
    onError: (error: Error) => {
      toast.error(`Delete failed: ${error.message}`);
    },
  });
}

// ---------------------------------------------------------------------------
// CLI runtime mutations
// ---------------------------------------------------------------------------

/** Rotate (regenerate) a CLI runtime token. The new value is returned once. */
export function useRotateCliRuntime() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, value }: { id: string; value?: string }) =>
      // Omit the second arg entirely on the generate path so the call signature
      // stays rotateCliCredential(id) (no explicit undefined).
      value === undefined ? rotateCliCredential(id) : rotateCliCredential(id, value),
    onSuccess: (_, { id, value }) => {
      toast.success(
        value && value.trim()
          ? "CLI token saved. Copy the value now"
          : "CLI token rotated. Copy the new value now",
      );
      void queryClient.invalidateQueries({ queryKey: secretsInventoryKeys.all });
      void queryClient.invalidateQueries({ queryKey: secretsCliKeys.byId(id) });
    },
    onError: (error: Error) => {
      toast.error(`Rotate failed: ${error.message}`);
    },
  });
}

/** Revoke (delete) a CLI runtime token. */
export function useRevokeCliRuntime() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) => revokeCliCredential(id),
    onSuccess: (_, { id }) => {
      toast.success("CLI token revoked");
      void queryClient.invalidateQueries({ queryKey: secretsInventoryKeys.all });
      void queryClient.invalidateQueries({ queryKey: secretsCliKeys.byId(id) });
    },
    onError: (error: Error) => {
      toast.error(`Revoke failed: ${error.message}`);
    },
  });
}

// ---------------------------------------------------------------------------
// Create credential mutations (bu-ayp6v.6)
// ---------------------------------------------------------------------------

/**
 * Create a new user credential (entity_info row) on the owner entity.
 *
 * Wraps POST /relationship/entities/{entityId}/info. Invalidates the
 * secrets inventory so the new entry appears in the spine immediately.
 */
export function useCreateUserSecret() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      entityId,
      request,
    }: {
      entityId: string;
      request: CreateEntityInfoRequest;
    }) => createEntityInfo(entityId, request),
    onSuccess: () => {
      toast.success("Credential saved");
      void queryClient.invalidateQueries({ queryKey: secretsInventoryKeys.all });
    },
    onError: (error: Error) => {
      toast.error(`Save failed: ${error.message}`);
    },
  });
}
