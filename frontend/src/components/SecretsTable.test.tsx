// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";

import type { SecretEntry } from "@/api/types";
import { buildSecretRows } from "@/lib/secrets-rows";
import { SecretsTable } from "@/components/secrets/SecretsTable";
import { useDeleteSecret } from "@/hooks/use-secrets";

vi.mock("@/hooks/use-secrets", () => ({
  useDeleteSecret: vi.fn(),
}));

vi.mock("@/components/ui/table", () => ({
  Table: ({ children }: { children: ReactNode }) => <table>{children}</table>,
  TableHeader: ({ children }: { children: ReactNode }) => <thead>{children}</thead>,
  TableBody: ({ children }: { children: ReactNode }) => <tbody>{children}</tbody>,
  TableRow: ({ children, className }: { children: ReactNode; className?: string }) => (
    <tr className={className}>{children}</tr>
  ),
  TableHead: ({ children, className }: { children: ReactNode; className?: string }) => (
    <th className={className}>{children}</th>
  ),
  TableCell: ({ children, className, colSpan }: { children: ReactNode; className?: string; colSpan?: number }) => (
    <td className={className} colSpan={colSpan}>{children}</td>
  ),
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({
    children,
    onClick,
    type = "button",
    className,
    disabled,
    title,
    "aria-label": ariaLabel,
  }: {
    children: ReactNode;
    onClick?: () => void;
    type?: "button" | "submit" | "reset";
    className?: string;
    disabled?: boolean;
    title?: string;
    "aria-label"?: string;
  }) => (
    <button
      type={type}
      className={className}
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={ariaLabel}
    >
      {children}
    </button>
  ),
}));

vi.mock("@/components/ui/badge", () => ({
  Badge: ({
    children,
    className,
  }: {
    children: ReactNode;
    className?: string;
  }) => <span className={className}>{children}</span>,
}));

vi.mock("@/components/ui/empty-state", () => ({
  EmptyState: ({
    title,
    description,
  }: {
    title: string;
    description: string;
  }) => (
    <div>
      <p>{title}</p>
      <p>{description}</p>
    </div>
  ),
}));

vi.mock("@/components/ui/skeleton", () => ({
  Skeleton: ({ className }: { className?: string }) => <div className={className}>Loading...</div>,
}));

vi.mock("@/components/ui/dialog", () => ({
  Dialog: () => null,
  DialogContent: () => null,
  DialogHeader: () => null,
  DialogTitle: () => null,
  DialogDescription: () => null,
  DialogFooter: () => null,
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function secret(overrides: Partial<SecretEntry> & Pick<SecretEntry, "key">): SecretEntry {
  return {
    key: overrides.key,
    category: overrides.category ?? "general",
    description: overrides.description ?? null,
    is_sensitive: overrides.is_sensitive ?? true,
    is_set: overrides.is_set ?? true,
    created_at: overrides.created_at ?? "2026-02-19T00:00:00Z",
    updated_at: overrides.updated_at ?? "2026-02-19T00:00:00Z",
    expires_at: overrides.expires_at ?? null,
    source: overrides.source ?? "database",
  };
}

describe("SecretsTable", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useDeleteSecret).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteSecret>);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  it("builds rows from templates + API without duplicate keys", () => {
    const rows = buildSecretRows([
      secret({
        key: "GOOGLE_CLIENT_ID",
        category: "google",
        description: "From API payload",
        source: "database",
      }),
    ]);

    const googleClientIdRows = rows.filter((row) => row.key.toUpperCase() === "GOOGLE_CLIENT_ID");
    expect(googleClientIdRows).toHaveLength(1);
    expect(googleClientIdRows[0].description).toBe("From API payload");
    expect(googleClientIdRows[0].rowState).toBe("local");
  });

  it("renders local, inherited, and missing states with override actions", () => {
    const handleEdit = vi.fn();
    const handleCreateOverride = vi.fn();

    act(() => {
      root.render(
        <SecretsTable
          butlerName="general"
          secrets={[
            secret({
              key: "ANTHROPIC_API_KEY",
              category: "core",
              source: "database",
              is_set: true,
            }),
            secret({
              key: "BUTLER_EMAIL_ADDRESS",
              category: "email",
              source: "shared",
              is_set: true,
            }),
          ]}
          isLoading={false}
          isError={false}
          onEdit={handleEdit}
          onCreateOverride={handleCreateOverride}
        />,
      );
    });

    expect(container.textContent).toContain("Local configured");
    expect(container.textContent).toContain("Inherited from shared");
    expect(container.textContent).toContain("Missing (null)");

    const overrideButton = container.querySelector(
      'button[aria-label="Override BUTLER_EMAIL_ADDRESS"]',
    ) as HTMLButtonElement | null;
    expect(overrideButton).not.toBeNull();

    act(() => {
      overrideButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(handleCreateOverride).toHaveBeenCalledWith({
      key: "BUTLER_EMAIL_ADDRESS",
      category: "email",
      description: "Butler email address",
    });

    const setValueButton = container.querySelector(
      'button[aria-label="Set BUTLER_TELEGRAM_TOKEN"]',
    ) as HTMLButtonElement | null;
    expect(setValueButton).not.toBeNull();
  });

  it("transitions inherited/missing rows to local after save-like updates", () => {
    const inheritedBefore = buildSecretRows([
      secret({
        key: "GOOGLE_CLIENT_SECRET",
        category: "google",
        source: "shared",
        is_set: true,
      }),
    ]).find((row) => row.key.toUpperCase() === "GOOGLE_CLIENT_SECRET");
    expect(inheritedBefore?.rowState).toBe("inherited");

    const localAfter = buildSecretRows([
      secret({
        key: "GOOGLE_CLIENT_SECRET",
        category: "google",
        source: "database",
        is_set: true,
      }),
    ]).find((row) => row.key.toUpperCase() === "GOOGLE_CLIENT_SECRET");
    expect(localAfter?.rowState).toBe("local");

    const missingBefore = buildSecretRows([]).find(
      (row) => row.key.toUpperCase() === "BUTLER_TELEGRAM_TOKEN",
    );
    expect(missingBefore?.rowState).toBe("missing");

    const localAfterSet = buildSecretRows([
      secret({
        key: "BUTLER_TELEGRAM_TOKEN",
        category: "telegram",
        source: "database",
        is_set: true,
      }),
    ]).find((row) => row.key.toUpperCase() === "BUTLER_TELEGRAM_TOKEN");
    expect(localAfterSet?.rowState).toBe("local");
  });
});
