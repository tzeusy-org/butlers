/**
 * Unit tests for use-secrets-mutations hooks (bu-ayp6v.2).
 *
 * Strategy: mock @tanstack/react-query's useMutation + useQueryClient, capture
 * the options object passed by each hook, then call onSuccess/onError directly
 * to verify toast messages and cache invalidation.
 *
 * Covers:
 *   - useRotateUserSecret: success toast + inventory + per-user invalidation
 *   - useDisconnectUserSecret: success toast + inventory + per-user invalidation
 *   - useProbeUserSecret: probe pass / probe fail toasts + invalidation
 *   - useSetSystemSecret: success toast + inventory + per-system invalidation
 *   - useProbeSystemSecret: probe pass / probe fail toasts + invalidation
 *   - useDeleteSystemSecret: success toast + inventory + per-system invalidation
 *   - useRotateCliRuntime: success toast + inventory + per-cli invalidation
 *   - useRevokeCliRuntime: success toast + inventory + per-cli invalidation
 *   - error paths: onError fires toast.error with error message
 */

import { describe, expect, it, vi, beforeEach } from "vitest";

// ---------------------------------------------------------------------------
// Mock @tanstack/react-query BEFORE importing hooks.
//
// vi.mock is hoisted, so factory variables must be defined with vi.fn() inline
// or pulled via vi.hoisted(). We capture useMutation calls after the fact
// by spying on the imported module post-mock.
// ---------------------------------------------------------------------------

const mockInvalidateQueries = vi.fn();
const mockQueryClient = { invalidateQueries: mockInvalidateQueries };

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useMutation: vi.fn((opts: unknown) => opts),
    useQueryClient: () => mockQueryClient,
  };
});

// ---------------------------------------------------------------------------
// Mock sonner toast
// ---------------------------------------------------------------------------

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

// ---------------------------------------------------------------------------
// Mock API client functions
// ---------------------------------------------------------------------------

vi.mock("@/api/client.ts", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/client.ts")>();
  return {
    ...original,
    rotateUserCredential: vi.fn(),
    disconnectUserCredential: vi.fn(),
    probeUserCredential: vi.fn(),
    setSystemCredential: vi.fn(),
    probeSystemCredential: vi.fn(),
    deleteSystemCredential: vi.fn(),
    rotateCliCredential: vi.fn(),
    revokeCliCredential: vi.fn(),
  };
});

// ---------------------------------------------------------------------------
// Import hooks and the mocked module AFTER mocks are set up.
// ---------------------------------------------------------------------------

import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  useRotateUserSecret,
  useDisconnectUserSecret,
  useProbeUserSecret,
  useSetSystemSecret,
  useProbeSystemSecret,
  useDeleteSystemSecret,
  useRotateCliRuntime,
  useRevokeCliRuntime,
  secretsUserKeys,
  secretsSystemKeys,
  secretsCliKeys,
} from "@/hooks/use-secrets-mutations";
import { secretsInventoryKeys } from "@/hooks/use-secrets-inventory.ts";

const mockUseMutation = vi.mocked(useMutation);
const mockToastSuccess = vi.mocked(toast.success);
const mockToastError = vi.mocked(toast.error);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Call the hook-under-test (which calls mockUseMutation) and return the
 * options object captured by the mock.
 */
function capturedMutationOptions(): {
  mutationFn: (...args: unknown[]) => unknown;
  onSuccess: (...args: unknown[]) => void;
  onError: (error: Error) => void;
} {
  const calls = mockUseMutation.mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  return calls[calls.length - 1][0] as ReturnType<typeof capturedMutationOptions>;
}

function makeProbeOk() {
  return { data: { ok: true, message: null, code: null, at: "" } };
}

function makeProbeFail(message = "token expired") {
  return { data: { ok: false, message, code: null, at: "" } };
}

// ---------------------------------------------------------------------------
// useRotateUserSecret
// ---------------------------------------------------------------------------

describe("useRotateUserSecret", () => {
  beforeEach(() => {
    mockUseMutation.mockClear();
    mockInvalidateQueries.mockClear();
    mockToastSuccess.mockClear();
    mockToastError.mockClear();
  });

  it("onSuccess invalidates inventory + per-user key and shows success toast", () => {
    useRotateUserSecret();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(undefined, { provider: "google", body: { value: "tok" }, identity: "tze-uuid" }, undefined);

    expect(mockToastSuccess).toHaveBeenCalledWith("Credential rotated");
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsInventoryKeys.all,
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsUserKeys.byProvider("google", "tze-uuid"),
    });
  });

  it("onSuccess without identity uses 'owner' default in per-user key", () => {
    useRotateUserSecret();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(undefined, { provider: "spotify", body: { value: "tok" } }, undefined);

    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsUserKeys.byProvider("spotify", undefined),
    });
  });

  it("onError shows error toast with message", () => {
    useRotateUserSecret();
    const { onError } = capturedMutationOptions();
    onError(new Error("network error"));

    expect(mockToastError).toHaveBeenCalledWith("Rotate failed: network error");
  });
});

// ---------------------------------------------------------------------------
// useDisconnectUserSecret
// ---------------------------------------------------------------------------

describe("useDisconnectUserSecret", () => {
  beforeEach(() => {
    mockUseMutation.mockClear();
    mockInvalidateQueries.mockClear();
    mockToastSuccess.mockClear();
    mockToastError.mockClear();
  });

  it("onSuccess invalidates inventory + per-user key and shows success toast", () => {
    useDisconnectUserSecret();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(undefined, { provider: "google", identity: "tze-uuid" }, undefined);

    expect(mockToastSuccess).toHaveBeenCalledWith("Credential disconnected");
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsInventoryKeys.all,
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsUserKeys.byProvider("google", "tze-uuid"),
    });
  });

  it("onError shows error toast with message", () => {
    useDisconnectUserSecret();
    const { onError } = capturedMutationOptions();
    onError(new Error("404 not found"));

    expect(mockToastError).toHaveBeenCalledWith("Disconnect failed: 404 not found");
  });
});

// ---------------------------------------------------------------------------
// useProbeUserSecret
// ---------------------------------------------------------------------------

describe("useProbeUserSecret", () => {
  beforeEach(() => {
    mockUseMutation.mockClear();
    mockInvalidateQueries.mockClear();
    mockToastSuccess.mockClear();
    mockToastError.mockClear();
  });

  it("onSuccess with ok=true shows success toast and invalidates caches", () => {
    useProbeUserSecret();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(makeProbeOk(), { provider: "google", identity: "tze-uuid" }, undefined);

    expect(mockToastSuccess).toHaveBeenCalledWith("Probe passed");
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsInventoryKeys.all,
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsUserKeys.byProvider("google", "tze-uuid"),
    });
  });

  it("onSuccess with ok=false shows error toast", () => {
    useProbeUserSecret();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(makeProbeFail("token expired"), { provider: "google" }, undefined);

    expect(mockToastError).toHaveBeenCalledWith("Probe failed: token expired");
    // Still invalidates even on probe failure
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsInventoryKeys.all,
    });
  });

  it("onError shows error toast with message", () => {
    useProbeUserSecret();
    const { onError } = capturedMutationOptions();
    onError(new Error("server error"));

    expect(mockToastError).toHaveBeenCalledWith("Probe failed: server error");
  });
});

// ---------------------------------------------------------------------------
// useSetSystemSecret
// ---------------------------------------------------------------------------

describe("useSetSystemSecret", () => {
  beforeEach(() => {
    mockUseMutation.mockClear();
    mockInvalidateQueries.mockClear();
    mockToastSuccess.mockClear();
    mockToastError.mockClear();
  });

  it("onSuccess invalidates inventory + per-system key and shows success toast", () => {
    useSetSystemSecret();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(undefined, { key: "ANTHROPIC_API_KEY", body: { value: "sk-xxx", target: "shared" } }, undefined);

    expect(mockToastSuccess).toHaveBeenCalledWith("Credential saved");
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsInventoryKeys.all,
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsSystemKeys.byKey("ANTHROPIC_API_KEY"),
    });
  });

  it("onError shows error toast with message", () => {
    useSetSystemSecret();
    const { onError } = capturedMutationOptions();
    onError(new Error("butler not registered"));

    expect(mockToastError).toHaveBeenCalledWith("Save failed: butler not registered");
  });
});

// ---------------------------------------------------------------------------
// useProbeSystemSecret
// ---------------------------------------------------------------------------

describe("useProbeSystemSecret", () => {
  beforeEach(() => {
    mockUseMutation.mockClear();
    mockInvalidateQueries.mockClear();
    mockToastSuccess.mockClear();
    mockToastError.mockClear();
  });

  it("onSuccess with ok=true shows success toast and invalidates caches", () => {
    useProbeSystemSecret();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(makeProbeOk(), { key: "ANTHROPIC_API_KEY" }, undefined);

    expect(mockToastSuccess).toHaveBeenCalledWith("Probe passed");
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsInventoryKeys.all,
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsSystemKeys.byKey("ANTHROPIC_API_KEY"),
    });
  });

  it("onSuccess with ok=false shows error toast and still invalidates caches", () => {
    useProbeSystemSecret();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(makeProbeFail("missing value"), { key: "OWNTRACKS_TOKEN" }, undefined);

    expect(mockToastError).toHaveBeenCalledWith("Probe failed: missing value");
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsInventoryKeys.all,
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsSystemKeys.byKey("OWNTRACKS_TOKEN"),
    });
  });

  it("onError shows error toast with message", () => {
    useProbeSystemSecret();
    const { onError } = capturedMutationOptions();
    onError(new Error("rate limited"));

    expect(mockToastError).toHaveBeenCalledWith("Probe failed: rate limited");
  });
});

// ---------------------------------------------------------------------------
// useDeleteSystemSecret
// ---------------------------------------------------------------------------

describe("useDeleteSystemSecret", () => {
  beforeEach(() => {
    mockUseMutation.mockClear();
    mockInvalidateQueries.mockClear();
    mockToastSuccess.mockClear();
    mockToastError.mockClear();
  });

  it("onSuccess invalidates inventory + per-system key and shows success toast", () => {
    useDeleteSystemSecret();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(undefined, { key: "OWNTRACKS_TOKEN", target: "shared" }, undefined);

    expect(mockToastSuccess).toHaveBeenCalledWith("Credential deleted");
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsInventoryKeys.all,
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsSystemKeys.byKey("OWNTRACKS_TOKEN"),
    });
  });

  it("onError shows error toast with message", () => {
    useDeleteSystemSecret();
    const { onError } = capturedMutationOptions();
    onError(new Error("not found"));

    expect(mockToastError).toHaveBeenCalledWith("Delete failed: not found");
  });
});

// ---------------------------------------------------------------------------
// useRotateCliRuntime
// ---------------------------------------------------------------------------

describe("useRotateCliRuntime", () => {
  beforeEach(() => {
    mockUseMutation.mockClear();
    mockInvalidateQueries.mockClear();
    mockToastSuccess.mockClear();
    mockToastError.mockClear();
  });

  it("onSuccess invalidates inventory + per-cli key and shows success toast", () => {
    useRotateCliRuntime();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(undefined, { id: "cli-auth/codex" }, undefined);

    expect(mockToastSuccess).toHaveBeenCalledWith("CLI token rotated. Copy the new value now");
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsInventoryKeys.all,
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsCliKeys.byId("cli-auth/codex"),
    });
  });

  it("onError shows error toast with message", () => {
    useRotateCliRuntime();
    const { onError } = capturedMutationOptions();
    onError(new Error("token not found"));

    expect(mockToastError).toHaveBeenCalledWith("Rotate failed: token not found");
  });
});

// ---------------------------------------------------------------------------
// useRevokeCliRuntime
// ---------------------------------------------------------------------------

describe("useRevokeCliRuntime", () => {
  beforeEach(() => {
    mockUseMutation.mockClear();
    mockInvalidateQueries.mockClear();
    mockToastSuccess.mockClear();
    mockToastError.mockClear();
  });

  it("onSuccess invalidates inventory + per-cli key and shows success toast", () => {
    useRevokeCliRuntime();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(undefined, { id: "cli-auth/codex" }, undefined);

    expect(mockToastSuccess).toHaveBeenCalledWith("CLI token revoked");
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsInventoryKeys.all,
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: secretsCliKeys.byId("cli-auth/codex"),
    });
  });

  it("onError shows error toast with message", () => {
    useRevokeCliRuntime();
    const { onError } = capturedMutationOptions();
    onError(new Error("already revoked"));

    expect(mockToastError).toHaveBeenCalledWith("Revoke failed: already revoked");
  });
});

// ---------------------------------------------------------------------------
// Query key shape contracts
// ---------------------------------------------------------------------------

describe("secretsUserKeys", () => {
  it("byProvider with identity produces scoped key", () => {
    expect(secretsUserKeys.byProvider("google", "tze-uuid")).toEqual([
      "secrets", "user", "google", "tze-uuid",
    ]);
  });

  it("byProvider without identity falls back to 'owner'", () => {
    expect(secretsUserKeys.byProvider("google")).toEqual([
      "secrets", "user", "google", "owner",
    ]);
  });
});

describe("secretsSystemKeys", () => {
  it("byKey produces scoped key", () => {
    expect(secretsSystemKeys.byKey("ANTHROPIC_API_KEY")).toEqual([
      "secrets", "system", "ANTHROPIC_API_KEY",
    ]);
  });
});

describe("secretsCliKeys", () => {
  it("byId produces scoped key", () => {
    expect(secretsCliKeys.byId("cli-auth/codex")).toEqual([
      "secrets", "cli", "cli-auth/codex",
    ]);
  });
});
