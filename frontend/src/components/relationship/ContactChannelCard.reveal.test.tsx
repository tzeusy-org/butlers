// @vitest-environment jsdom
/**
 * ContactChannelCard — secured reveal click-routing tests (bu-6m9an)
 *
 * Verifies that clicking "Reveal" in SecuredChannelEntry dispatches to the
 * correct mutation based on entry.source:
 *   source="entity_facts" → revealEntityMutation.mutate({ entityId, infoId })
 *   source=null (legacy)  → revealContactMutation.mutate({ contactId, infoId })
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
import { useRevealContactSecret } from "@/hooks/use-contacts";
import type { ContactInfoEntry } from "@/api/types";

// ---------------------------------------------------------------------------
// Module mocks (mirror ContactChannelCard.test.tsx)
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-entities", () => ({
  useEntityLinkedContacts: vi.fn(),
  useAddEntityContact: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useDeleteEntityContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useUpdateEntityContact: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useRevealEntityContactSecret: vi.fn(() => ({ mutate: vi.fn() })),
}));

vi.mock("@/hooks/use-contacts", () => ({
  useRevealContactSecret: vi.fn(() => ({ mutate: vi.fn() })),
  usePatchContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
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

const CI_SECURED_LEGACY: ContactInfoEntry = {
  id: "ci-031",
  type: "other",
  value: null,
  is_primary: false,
  secured: true,
  parent_id: null,
  context: null,
  source: null,
};

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function renderRow(
  entry: ContactInfoEntry,
  contactId = "contact-001",
  entityId = "entity-001",
) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ExpandedContactInfoRow
          entry={entry}
          contactId={contactId}
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
  let contactMutate: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    entityMutate = vi.fn();
    contactMutate = vi.fn();
    vi.mocked(useRevealEntityContactSecret).mockReturnValue(
      { mutate: entityMutate } as unknown as ReturnType<typeof useRevealEntityContactSecret>,
    );
    vi.mocked(useRevealContactSecret).mockReturnValue(
      { mutate: contactMutate } as unknown as ReturnType<typeof useRevealContactSecret>,
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
    renderRow(CI_SECURED_ENTITY_FACTS, "contact-005", "entity-005");
    fireEvent.click(screen.getByText("Reveal"));
    expect(entityMutate).toHaveBeenCalledOnce();
    expect(entityMutate).toHaveBeenCalledWith(
      { entityId: "entity-005", infoId: "ci-032" },
      expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
    );
  });

  it("clicking Reveal does NOT call the contact-keyed mutate for entity_facts entries", () => {
    renderRow(CI_SECURED_ENTITY_FACTS, "contact-005", "entity-005");
    fireEvent.click(screen.getByText("Reveal"));
    expect(contactMutate).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Tests: click-routing for legacy (source=null) secured entries
// ---------------------------------------------------------------------------

describe("SecuredChannelEntry — Reveal click routing (legacy contact_info)", () => {
  let entityMutate: ReturnType<typeof vi.fn>;
  let contactMutate: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    entityMutate = vi.fn();
    contactMutate = vi.fn();
    vi.mocked(useRevealEntityContactSecret).mockReturnValue(
      { mutate: entityMutate } as unknown as ReturnType<typeof useRevealEntityContactSecret>,
    );
    vi.mocked(useRevealContactSecret).mockReturnValue(
      { mutate: contactMutate } as unknown as ReturnType<typeof useRevealContactSecret>,
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

  it("clicking Reveal calls the contact-keyed mutate with { contactId, infoId }", () => {
    renderRow(CI_SECURED_LEGACY, "contact-004", "entity-004");
    fireEvent.click(screen.getByText("Reveal"));
    expect(contactMutate).toHaveBeenCalledOnce();
    expect(contactMutate).toHaveBeenCalledWith(
      { contactId: "contact-004", infoId: "ci-031" },
      expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
    );
  });

  it("clicking Reveal does NOT call the entity-keyed mutate for legacy entries", () => {
    renderRow(CI_SECURED_LEGACY, "contact-004", "entity-004");
    fireEvent.click(screen.getByText("Reveal"));
    expect(entityMutate).not.toHaveBeenCalled();
  });
});
