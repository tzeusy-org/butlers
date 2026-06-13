// @vitest-environment jsdom
/**
 * ContactChannelCard — secured reveal click tests
 *
 * Verifies that clicking "Reveal" in SecuredChannelEntry dispatches to
 * revealEntityMutation.mutate({ entityId, infoId }).
 *
 * All entries from list_entity_linked_contacts carry source="entity_facts"
 * (public.contact_info was dropped in bu-e2ja9), so all reveals route to
 * the entity-keyed endpoint via useRevealEntityContactSecret.
 *
 * Uses @testing-library/react for DOM interaction (fireEvent.click).
 * Split from ContactChannelCard.test.tsx (which uses renderToStaticMarkup)
 * so jsdom environment is scoped to interaction tests only.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ExpandedContactInfoRow } from "@/components/relationship/ContactChannelCard";
import {
  useDeleteEntityContact,
  useUpdateEntityContact,
  useRevealEntityContactSecret,
} from "@/hooks/use-entities";
import type { ContactInfoEntry } from "@/api/types";

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-entities", () => ({
  useEntityLinkedContacts: vi.fn(),
  useAddEntityContact: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useDeleteEntityContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useUpdateEntityContact: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useRevealEntityContactSecret: vi.fn(() => ({ mutate: vi.fn() })),
  useSetPreferredChannel: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useClearPreferredChannel: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CI_SECURED_ENTITY_FACTS: ContactInfoEntry = {
  id: "ci-032",
  type: "other",
  value: null,
  is_primary: false,
  secured: true,
  parent_id: null,
  context: null,
  source: "entity_facts",
  predicate: "has-handle",
  value_hash: null,
};

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function renderRow(
  entry: ContactInfoEntry,
  entityId = "entity-001",
) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ExpandedContactInfoRow
          entry={entry}
          entityId={entityId}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests: click-routing for entity_facts secured entries
// ---------------------------------------------------------------------------

describe("SecuredChannelEntry — Reveal click routing (entity_facts)", () => {
  let entityMutate: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    entityMutate = vi.fn();
    vi.mocked(useRevealEntityContactSecret).mockReturnValue(
      { mutate: entityMutate } as unknown as ReturnType<typeof useRevealEntityContactSecret>,
    );
    vi.mocked(useDeleteEntityContact).mockReturnValue(
      { mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useDeleteEntityContact>,
    );
    vi.mocked(useUpdateEntityContact).mockReturnValue(
      { mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateEntityContact>,
    );
  });

  afterEach(() => {
    cleanup();
  });

  it("clicking Reveal calls the entity-keyed mutate with { entityId, infoId }", () => {
    renderRow(CI_SECURED_ENTITY_FACTS, "entity-005");
    fireEvent.click(screen.getByText("Reveal"));
    expect(entityMutate).toHaveBeenCalledOnce();
    expect(entityMutate).toHaveBeenCalledWith(
      { entityId: "entity-005", infoId: "ci-032" },
      expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
    );
  });
});
