// @vitest-environment jsdom
/**
 * Entity graph dossier panel (bu-8cdl1.8, REQ-entity-graph-011): receipts and known/withheld
 * counts from the API, distinct loading, empty and unavailable states, and a read-only retry.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { EntityDossierPanel } from "@/components/relationship/EntityDossierPanel";
import type { EntityDossier } from "@/api/entity-dossier.ts";

const mockGetEntityDossier = vi.fn();

vi.mock("@/api/entity-dossier.ts", () => ({
  getEntityDossier: (...args: unknown[]) => mockGetEntityDossier(...args),
}));

function dossier(overrides: Partial<EntityDossier> = {}): EntityDossier {
  return {
    entity_id: "e1",
    relationships_known: 2,
    relationships_withheld: 3,
    receipts_limit: 100,
    receipts_truncated: false,
    sources: [
      {
        source_schema: "relationship",
        source_table: "entity_facts",
        live_count: 2,
        receipts: [
          {
            edge_id: "edge-1",
            source_id: "src-1",
            predicate: "friend_of",
            direction: "subject",
            counterpart_entity_id: "e2",
            counterpart_name: "Alex Friend",
            sensitivity: "normal",
            updated_at: "2026-10-01T00:00:00Z",
          },
          {
            edge_id: "edge-2",
            source_id: "src-2",
            predicate: "manages",
            direction: "object",
            counterpart_entity_id: "e3",
            counterpart_name: null,
            sensitivity: "normal",
            updated_at: "2026-10-02T00:00:00Z",
          },
        ],
      },
    ],
    ...overrides,
  };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <EntityDossierPanel entityId="e1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  mockGetEntityDossier.mockReset();
});

// REQ-entity-graph-011: panel states against a mocked dossier API.
describe("EntityDossierPanel", () => {
  it("shows a loading state before the dossier arrives, not an empty one", () => {
    mockGetEntityDossier.mockReturnValue(new Promise(() => undefined));
    renderPanel();

    expect(screen.getByTestId("entity-dossier-loading")).toBeTruthy();
    expect(screen.queryByTestId("entity-dossier-empty")).toBeNull();
  });

  it("renders per-source receipts and the known/withheld counts", async () => {
    mockGetEntityDossier.mockResolvedValue({ data: dossier(), meta: {} });
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("entity-dossier-coverage")).toBeTruthy());
    expect(screen.getByTestId("entity-dossier-coverage").textContent).toBe(
      "2 relationships known, 3 withheld for sensitivity",
    );
    expect(screen.getByText("relationship.entity_facts")).toBeTruthy();
    const friend = screen.getByRole("link", { name: "Alex Friend" });
    expect(friend.getAttribute("href")).toBe("/entities/e2");
    expect(screen.getByText("friend_of")).toBeTruthy();
    // A counterpart with no name falls back to its id rather than a made-up label.
    expect(screen.getByRole("link", { name: "e3" })).toBeTruthy();
  });

  it("states withheld relationships as a count only", async () => {
    mockGetEntityDossier.mockResolvedValue({
      data: dossier({ relationships_known: 0, relationships_withheld: 4, sources: [] }),
      meta: {},
    });
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("entity-dossier-coverage")).toBeTruthy());
    expect(screen.getByTestId("entity-dossier-coverage").textContent).toBe(
      "0 relationships known, 4 withheld for sensitivity",
    );
    expect(screen.queryByTestId("entity-dossier-source")).toBeNull();
    expect(screen.queryByTestId("entity-dossier-empty")).toBeNull();
  });

  it("shows a distinct empty state for an entity with no relationships at all", async () => {
    mockGetEntityDossier.mockResolvedValue({
      data: dossier({ relationships_known: 0, relationships_withheld: 0, sources: [] }),
      meta: {},
    });
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("entity-dossier-empty")).toBeTruthy());
    expect(screen.queryByTestId("entity-dossier-error")).toBeNull();
  });

  it("shows unavailable with a retry that re-reads, never an empty dossier", async () => {
    const user = userEvent.setup();
    mockGetEntityDossier.mockRejectedValueOnce(new Error("503"));
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("entity-dossier-error")).toBeTruthy());
    expect(screen.queryByTestId("entity-dossier-empty")).toBeNull();

    mockGetEntityDossier.mockResolvedValue({ data: dossier(), meta: {} });
    await user.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(screen.getByTestId("entity-dossier-coverage")).toBeTruthy());
    expect(mockGetEntityDossier).toHaveBeenCalledTimes(2);
    expect(mockGetEntityDossier).toHaveBeenLastCalledWith("e1");
  });

  it("says when the receipts list was cut short while the counts stay true", async () => {
    mockGetEntityDossier.mockResolvedValue({
      data: dossier({ receipts_truncated: true, receipts_limit: 1 }),
      meta: {},
    });
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("entity-dossier-truncated")).toBeTruthy());
    expect(screen.getByTestId("entity-dossier-truncated").textContent).toContain("newest 1");
  });
});
