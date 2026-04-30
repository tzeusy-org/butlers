// @vitest-environment jsdom

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import EntityDetailView from "@/components/relationship/EntityDetailView";
import ContactDetailView from "@/components/relationship/ContactDetailView";
import {
  useEntityGifts,
  useEntityInteractions,
  useEntityLinkedContacts,
  useEntityLoans,
  useEntityNotes,
  useEntityTimeline,
} from "@/hooks/use-entities";
import type {
  ContactDetail,
  EntityGift,
  EntityLoan,
  RelationshipEntityDetail,
} from "@/api/types";

// ---------------------------------------------------------------------------
// Mock all entity hooks — tests control return values directly
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-entities", () => ({
  useEntityLinkedContacts: vi.fn(),
  useEntityNotes: vi.fn(),
  useEntityInteractions: vi.fn(),
  useEntityGifts: vi.fn(),
  useEntityLoans: vi.fn(),
  useEntityTimeline: vi.fn(),
}));

// Hooks used by ContactDetailView
vi.mock("@/hooks/use-contacts", () => ({
  useCreateContactInfo: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useDeleteContact: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useDeleteContactInfo: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePatchContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePatchContactInfo: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useRevealContactSecret: vi.fn(() => ({ mutate: vi.fn() })),
}));

vi.mock("@/hooks/use-memory", () => ({
  useUnlinkContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const BASE_ENTITY: RelationshipEntityDetail = {
  id: "entity-001",
  canonical_name: "Alice Example",
  entity_type: "person",
  aliases: [],
  roles: [],
  metadata: {},
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
};

const BASE_CONTACT: ContactDetail = {
  id: "contact-001",
  full_name: "Alice Example",
  first_name: "Alice",
  last_name: "Example",
  nickname: null,
  email: "alice@example.com",
  phone: null,
  labels: [],
  last_interaction_at: null,
  notes: null,
  birthday: null,
  company: null,
  job_title: null,
  address: null,
  metadata: {},
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
  roles: [],
  entity_id: null,
  contact_info: [],
  preferred_channel: null,
};

// ---------------------------------------------------------------------------
// Mock setup helpers
// ---------------------------------------------------------------------------

function setAllTabsEmpty() {
  vi.mocked(useEntityLinkedContacts).mockReturnValue({
    data: [],
    isLoading: false,
  } as unknown as ReturnType<typeof useEntityLinkedContacts>);
  vi.mocked(useEntityNotes).mockReturnValue({
    data: [],
    isLoading: false,
  } as unknown as ReturnType<typeof useEntityNotes>);
  vi.mocked(useEntityInteractions).mockReturnValue({
    data: [],
    isLoading: false,
  } as unknown as ReturnType<typeof useEntityInteractions>);
  vi.mocked(useEntityGifts).mockReturnValue({
    data: [],
    isLoading: false,
  } as unknown as ReturnType<typeof useEntityGifts>);
  vi.mocked(useEntityLoans).mockReturnValue({
    data: [],
    isLoading: false,
  } as unknown as ReturnType<typeof useEntityLoans>);
  vi.mocked(useEntityTimeline).mockReturnValue({
    data: [],
    isLoading: false,
  } as unknown as ReturnType<typeof useEntityTimeline>);
}

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

function findTabTrigger(container: HTMLElement, label: string): HTMLElement | undefined {
  return Array.from(container.querySelectorAll<HTMLElement>('[role="tab"]')).find(
    (el) => el.textContent?.trim() === label,
  );
}

function clickTab(container: HTMLElement, label: string) {
  const trigger = findTabTrigger(container, label);
  expect(trigger).toBeDefined();
  // Radix Tabs uses onMouseDown (button=0, no ctrlKey) to switch tabs.
  act(() => {
    trigger!.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0 }));
  });
}

// ---------------------------------------------------------------------------
// Tests — Scenario 1: All 5 tabs render + empty states
// ---------------------------------------------------------------------------

describe("EntityDetailView — tab rendering and empty states", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    setAllTabsEmpty();
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

  function renderView(entity: RelationshipEntityDetail = BASE_ENTITY) {
    act(() => {
      const queryClient = new QueryClient();
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <EntityDetailView entity={entity} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("renders all 5 tab triggers: Notes, Interactions, Gifts, Loans, Timeline", () => {
    renderView();

    expect(findTabTrigger(container, "Notes")).toBeDefined();
    expect(findTabTrigger(container, "Interactions")).toBeDefined();
    expect(findTabTrigger(container, "Gifts")).toBeDefined();
    expect(findTabTrigger(container, "Loans")).toBeDefined();
    expect(findTabTrigger(container, "Timeline")).toBeDefined();
  });

  it("shows empty-state message for Notes tab (default active tab)", () => {
    renderView();
    expect(container.textContent).toContain("No notes for this entity yet.");
  });

  it("shows empty-state message for Interactions tab when no interactions exist", () => {
    renderView();
    clickTab(container, "Interactions");
    expect(container.textContent).toContain("No interactions recorded for this entity.");
  });

  it("shows empty-state message for Gifts tab when no gifts exist", () => {
    renderView();
    clickTab(container, "Gifts");
    expect(container.textContent).toContain("No gifts recorded for this entity.");
  });

  it("shows empty-state message for Loans tab when no loans exist", () => {
    renderView();
    clickTab(container, "Loans");
    expect(container.textContent).toContain("No loans recorded for this entity.");
  });

  it("shows empty-state message for Timeline tab when no events exist", () => {
    renderView();
    clickTab(container, "Timeline");
    expect(container.textContent).toContain("No timeline events for this entity yet.");
  });
});

// ---------------------------------------------------------------------------
// Tests — Scenario 2: Gift facts render with correct field mappings
// ---------------------------------------------------------------------------

describe("EntityDetailView — gift facts render with correct fields", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    setAllTabsEmpty();
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

  function renderView(entity: RelationshipEntityDetail = BASE_ENTITY) {
    act(() => {
      const queryClient = new QueryClient();
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <EntityDetailView entity={entity} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("renders gift description, occasion, and status in the Gifts tab", () => {
    const gift: EntityGift = {
      id: "gift-001",
      description: "A lovely book",
      occasion: "Birthday",
      status: "given",
      created_at: "2025-06-15T00:00:00Z",
    };
    vi.mocked(useEntityGifts).mockReturnValue({
      data: [gift],
      isLoading: false,
    } as ReturnType<typeof useEntityGifts>);

    renderView();
    clickTab(container, "Gifts");

    expect(container.textContent).toContain("A lovely book");
    expect(container.textContent).toContain("Birthday");
    expect(container.textContent).toContain("given");
  });

  it("renders '—' placeholders when gift fields are null", () => {
    const gift: EntityGift = {
      id: "gift-002",
      description: null,
      occasion: null,
      status: null,
      created_at: null,
    };
    vi.mocked(useEntityGifts).mockReturnValue({
      data: [gift],
      isLoading: false,
    } as ReturnType<typeof useEntityGifts>);

    renderView();
    clickTab(container, "Gifts");

    // Table renders (no empty-state message) and uses '—' for nulls
    expect(container.textContent).not.toContain("No gifts recorded for this entity.");
    expect(container.innerHTML).toContain("—");
  });
});

// ---------------------------------------------------------------------------
// Tests — Scenario 2 (cont.): Loan facts render with correct field mappings
// ---------------------------------------------------------------------------

describe("EntityDetailView — loan facts render with correct fields", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    setAllTabsEmpty();
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

  function renderView(entity: RelationshipEntityDetail = BASE_ENTITY) {
    act(() => {
      const queryClient = new QueryClient();
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <EntityDetailView entity={entity} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("renders loan description, amount_cents, currency, direction, and settled status", () => {
    const loan: EntityLoan = {
      id: "loan-001",
      description: "Borrowed my camera",
      amount_cents: "15000",
      currency: "USD",
      direction: "lent",
      settled: "false",
      settled_at: null,
      created_at: "2025-03-10T00:00:00Z",
    };
    vi.mocked(useEntityLoans).mockReturnValue({
      data: [loan],
      isLoading: false,
    } as ReturnType<typeof useEntityLoans>);

    renderView();
    clickTab(container, "Loans");

    expect(container.textContent).toContain("Borrowed my camera");
    expect(container.textContent).toContain("15000");
    expect(container.textContent).toContain("USD");
    expect(container.textContent).toContain("lent");
    // settled="false" renders as "active" badge
    expect(container.textContent).toContain("active");
  });

  it("renders settled loan with 'settled' badge when settled is 'true'", () => {
    const loan: EntityLoan = {
      id: "loan-002",
      description: "Repaid lunch",
      amount_cents: "2000",
      currency: "EUR",
      direction: "borrowed",
      settled: "true",
      settled_at: "2025-04-01T00:00:00Z",
      created_at: "2025-03-01T00:00:00Z",
    };
    vi.mocked(useEntityLoans).mockReturnValue({
      data: [loan],
      isLoading: false,
    } as ReturnType<typeof useEntityLoans>);

    renderView();
    clickTab(container, "Loans");

    expect(container.textContent).toContain("Repaid lunch");
    expect(container.textContent).toContain("settled");
  });
});

// ---------------------------------------------------------------------------
// Tests — Scenario 3: Contact with null entity_id shows warning banner
// ---------------------------------------------------------------------------

describe("ContactDetailView — null entity_id warning banner", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  function renderContactView(contact: ContactDetail): string {
    const queryClient = new QueryClient();
    return renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <ContactDetailView contact={contact} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("renders warning banner when contact has no linked entity (entity_id is null)", () => {
    const html = renderContactView({ ...BASE_CONTACT, entity_id: null });

    expect(html).toContain("This contact is not linked to an entity");
    expect(html).toContain("Activity history is unavailable");
  });

  it("does not render the warning banner when contact has a linked entity", () => {
    const html = renderContactView({ ...BASE_CONTACT, entity_id: "entity-001" });

    expect(html).not.toContain("This contact is not linked to an entity");
  });

  it("renders a link to the entity detail page when entity_id is present", () => {
    const html = renderContactView({ ...BASE_CONTACT, entity_id: "entity-abc" });

    expect(html).toContain("/butlers/relationship/entities/entity-abc");
  });
});
