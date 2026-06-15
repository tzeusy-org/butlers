/**
 * ContactChannelCard tests
 *
 * Covers:
 * - Entity with one linked contact (populated state)
 * - Entity with multiple linked contacts (multi-contact stacking)
 * - Entity with sparse contact (no labels, no preferred_channel, one channel)
 * - Entity with secured contact_info (entity_facts secured entry — reveal via
 *   entity-keyed endpoint, public.contact_info was dropped in bu-e2ja9)
 * - Entity with zero linked contacts (empty-state with Link contact CTA)
 * - ExpandedContactInfoRow: edit/delete affordances present for entity_facts
 *   entries; read-only (legacy marker) for source=null entries (compat display
 *   only — the live API no longer returns source=null entries)
 * - ExpandedContactInfoRow: edit button is ENABLED for entity_facts rows and
 *   calls useUpdateEntityContact (bu-690xu)
 * - ExpandedContactInfoRow: delete mutation wired to useDeleteEntityContact
 * - AddChannelInfoForm: add mutation wired to useAddEntityContact
 * - Secured reveal: source="entity_facts" entries → useRevealEntityContactSecret
 * - sortChannelsPrimaryFirst: primary-first ordering unit tests (bu-dvquo)
 * - ContactRow: primary-first rendering in collapsed + expanded views (bu-dvquo)
 * - Amber unverified dot: degraded-honest (no dot — ContactInfoEntry lacks
 *   per-channel verified field; follow-up required) (bu-dvquo)
 *
 * IMPORTANT: Secured reveal tests assert that the secret value does NOT appear
 * in the masked render. They DO NOT assert the actual secret value to prevent
 * it from leaking into snapshot text.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ContactChannelCard, ExpandedContactInfoRow } from "@/components/relationship/ContactChannelCard";
import { sortChannelsPrimaryFirst } from "@/components/relationship/contact-channel-utils";
import { useEntityLinkedContacts, useAddEntityContact, useDeleteEntityContact, useMarkEntityContactVerified, useUpdateEntityContact, useRevealEntityContactSecret } from "@/hooks/use-entities";
import type { LinkedContactSummary, ContactInfoEntry } from "@/api/types";

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-entities", () => ({
  useEntityLinkedContacts: vi.fn(),
  useAddEntityContact: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useDeleteEntityContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useMarkEntityContactVerified: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useUpdateEntityContact: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useRevealEntityContactSecret: vi.fn(() => ({ mutate: vi.fn() })),
  useSetPreferredChannel: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useClearPreferredChannel: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function setLinkedContacts(
  contacts: LinkedContactSummary[],
  opts: { isLoading?: boolean } = {},
) {
  vi.mocked(useEntityLinkedContacts).mockReturnValue({
    data: contacts,
    isLoading: opts.isLoading ?? false,
  } as ReturnType<typeof useEntityLinkedContacts>);
}

function renderCard(entityId = "entity-001", onLinkContact?: () => void): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ContactChannelCard
          entityId={entityId}
          onLinkContact={onLinkContact}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderExpandedRow(entry: ContactInfoEntry, entityId = "entity-001"): string {
  return renderToStaticMarkup(
    <ExpandedContactInfoRow
      entry={entry}
      entityId={entityId}
    />,
  );
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

// Legacy contact_info entry (source=null, write-blocked since PR #2021).
const CI_LEGACY_EMAIL: ContactInfoEntry = {
  id: "ci-001",
  type: "email",
  value: "alice@example.com",
  is_primary: true,
  secured: false,
  parent_id: null,
  context: null,
  source: null,
};

// Entity-facts-sourced entry (source="entity_facts", entity-keyed mutations available).
const CI_ENTITY_FACTS_TELEGRAM: ContactInfoEntry = {
  id: "ci-002",
  type: "telegram",
  value: "@alice_tg",
  is_primary: false,
  secured: false,
  parent_id: null,
  context: null,
  source: "entity_facts",
  predicate: "has-handle",
  value_hash: "abcdef0123456789",
};

const CI_PHONE_ENTITY_FACTS: ContactInfoEntry = {
  id: "ci-010",
  type: "phone",
  value: "555-0100",
  is_primary: true,
  secured: false,
  parent_id: null,
  context: null,
  source: "entity_facts",
  predicate: "has-phone",
  value_hash: "fedcba9876543210",
};

const CI_WEBSITE: ContactInfoEntry = {
  id: "ci-020",
  type: "website",
  value: "https://charlie.example.com",
  is_primary: false,
  secured: false,
  parent_id: null,
  context: null,
  source: "entity_facts",
  predicate: "has-website",
  value_hash: "1234567890abcdef",
};

// Secured entity_info row (source="entity_facts") — reveal via entity-keyed endpoint.
// All secured entries surfaced by list_entity_linked_contacts carry
// source="entity_facts" (public.contact_info was dropped in bu-e2ja9).
const CI_SECURED_ENTITY_FACTS: ContactInfoEntry = {
  id: "ci-032",
  type: "other",
  value: null, // secured — value is null until revealed
  is_primary: false,
  secured: true,
  parent_id: null,
  context: null,
  source: "entity_facts",
  predicate: "has-handle",
  value_hash: null, // secured entries have no visible value_hash
};

const CONTACT_ONE: LinkedContactSummary = {
  id: "contact-001",
  full_name: "Alice Smith",
  email: "alice@example.com",
  phone: null,
  contact_info: [CI_LEGACY_EMAIL, CI_ENTITY_FACTS_TELEGRAM],
  labels: [
    { id: "label-001", name: "Friend", color: null },
  ],
  preferred_channel: "telegram",
  reachable_channels: ["email", "telegram"],
};

const CONTACT_TWO: LinkedContactSummary = {
  id: "contact-002",
  full_name: "Bob Jones",
  email: "bob@example.com",
  phone: "555-0100",
  contact_info: [CI_PHONE_ENTITY_FACTS],
  labels: [
    { id: "label-002", name: "Work", color: "#1a73e8" },
  ],
  preferred_channel: null,
  reachable_channels: [],
};

const SPARSE_CONTACT: LinkedContactSummary = {
  id: "contact-003",
  full_name: "Charlie",
  email: null,
  phone: null,
  contact_info: [CI_WEBSITE],
  labels: [],
  preferred_channel: null,
  reachable_channels: [],
};

const SECURED_CONTACT: LinkedContactSummary = {
  id: "contact-004",
  full_name: "Diana Prince",
  email: "diana@example.com",
  phone: null,
  contact_info: [
    {
      id: "ci-030",
      type: "email",
      value: "diana@example.com",
      is_primary: true,
      secured: false,
      parent_id: null,
      context: null,
      source: "entity_facts",
      predicate: "has-email",
      value_hash: "aabbccddeeff0011",
    },
    CI_SECURED_ENTITY_FACTS,
  ],
  labels: [],
  preferred_channel: null,
  reachable_channels: ["email"],
};

// Contact with a secured entity_facts entry (bu-6m9an dual-dispatch).
// Simulates a secured row from public.entity_info that has been surfaced
// through the linked-contacts endpoint (post bu-pl8fy migration).
const SECURED_ENTITY_FACTS_CONTACT: LinkedContactSummary = {
  id: "contact-005",
  full_name: "Eve Adams",
  email: "eve@example.com",
  phone: null,
  contact_info: [
    {
      id: "ci-040",
      type: "email",
      value: "eve@example.com",
      is_primary: true,
      secured: false,
      parent_id: null,
      context: null,
      source: "entity_facts",
      predicate: "has-email",
      value_hash: "99aabbccddeeff00",
    },
    CI_SECURED_ENTITY_FACTS,
  ],
  labels: [],
  preferred_channel: null,
  reachable_channels: ["email"],
};

// ---------------------------------------------------------------------------
// Tests: populated state — one linked contact
// ---------------------------------------------------------------------------

describe("ContactChannelCard — one linked contact (populated state)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useAddEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useAddEntityContact>);
    vi.mocked(useDeleteEntityContact).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useDeleteEntityContact>);
    vi.mocked(useUpdateEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateEntityContact>);
  });

  it("renders the Channels heading", () => {
    setLinkedContacts([CONTACT_ONE]);
    const html = renderCard();
    expect(html).toContain("Channels");
  });

  it("renders the contact name", () => {
    setLinkedContacts([CONTACT_ONE]);
    const html = renderCard();
    expect(html).toContain("Alice Smith");
  });

  it("renders label chips for the contact", () => {
    setLinkedContacts([CONTACT_ONE]);
    const html = renderCard();
    expect(html).toContain("Friend");
  });

  it("renders preferred channel chip when set", () => {
    setLinkedContacts([CONTACT_ONE]);
    const html = renderCard();
    // preferred_channel = "telegram" → rendered as "Telegram" in the badge
    expect(html).toContain("Telegram");
  });

  it("renders channel chips for non-secured contact_info entries in collapsed view", () => {
    setLinkedContacts([CONTACT_ONE]);
    const html = renderCard();
    // email chip with value
    expect(html).toContain("Email");
    expect(html).toContain("alice@example.com");
  });

  it("renders a contact row with data-testid", () => {
    setLinkedContacts([CONTACT_ONE]);
    const html = renderCard();
    expect(html).toContain('data-testid="contact-row-contact-001"');
  });

  it("does NOT render the empty-state when contacts exist", () => {
    setLinkedContacts([CONTACT_ONE]);
    const html = renderCard();
    expect(html).not.toContain('data-testid="contact-channel-empty-state"');
    expect(html).not.toContain("Link contact");
  });

  it("card section has the correct data-testid", () => {
    setLinkedContacts([CONTACT_ONE]);
    const html = renderCard();
    expect(html).toContain('data-testid="contact-channel-card"');
  });
});

// ---------------------------------------------------------------------------
// Tests: multi-contact stacking
// ---------------------------------------------------------------------------

describe("ContactChannelCard — multi-contact stacking", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useAddEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useAddEntityContact>);
    vi.mocked(useDeleteEntityContact).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useDeleteEntityContact>);
    vi.mocked(useUpdateEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateEntityContact>);
  });

  it("renders both contact names when two contacts are linked", () => {
    setLinkedContacts([CONTACT_ONE, CONTACT_TWO]);
    const html = renderCard();
    expect(html).toContain("Alice Smith");
    expect(html).toContain("Bob Jones");
  });

  it("renders a separate row for each contact", () => {
    setLinkedContacts([CONTACT_ONE, CONTACT_TWO]);
    const html = renderCard();
    expect(html).toContain('data-testid="contact-row-contact-001"');
    expect(html).toContain('data-testid="contact-row-contact-002"');
  });

  it("renders labels for both contacts", () => {
    setLinkedContacts([CONTACT_ONE, CONTACT_TWO]);
    const html = renderCard();
    expect(html).toContain("Friend");
    expect(html).toContain("Work");
  });

  it("renders channel chips for both contacts", () => {
    setLinkedContacts([CONTACT_ONE, CONTACT_TWO]);
    const html = renderCard();
    // Alice has email + telegram; Bob has phone
    expect(html).toContain("alice@example.com");
    expect(html).toContain("555-0100");
  });
});

// ---------------------------------------------------------------------------
// Tests: sparse contact (no labels, no preferred_channel, one channel)
// ---------------------------------------------------------------------------

describe("ContactChannelCard — sparse contact", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useAddEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useAddEntityContact>);
    vi.mocked(useDeleteEntityContact).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useDeleteEntityContact>);
    vi.mocked(useUpdateEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateEntityContact>);
  });

  it("renders the contact name", () => {
    setLinkedContacts([SPARSE_CONTACT]);
    const html = renderCard();
    expect(html).toContain("Charlie");
  });

  it("renders without errors when labels and preferred_channel are absent", () => {
    setLinkedContacts([SPARSE_CONTACT]);
    expect(() => renderCard()).not.toThrow();
    const html = renderCard();
    expect(html).toContain('data-testid="contact-channel-card"');
  });

  it("renders the single website channel chip", () => {
    setLinkedContacts([SPARSE_CONTACT]);
    const html = renderCard();
    expect(html).toContain("Website");
    expect(html).toContain("https://charlie.example.com");
  });

  it("does NOT render label chips when labels array is empty", () => {
    setLinkedContacts([SPARSE_CONTACT]);
    const html = renderCard();
    expect(html).not.toContain("Friend");
    expect(html).not.toContain("Work");
  });
});

// ---------------------------------------------------------------------------
// Tests: secured contact_info entries (reveal/hide cycle)
//
// IMPORTANT: We assert the MASKED state renders correctly, and that the
// "Reveal" button is present. We do NOT assert any actual secret value in
// the snapshot text to avoid leaking secrets.
//
// NOTE: renderToStaticMarkup renders the initial (masked) state only.
// The reveal interaction requires a live DOM and is excluded from server-side
// static rendering tests. The SecuredChannelEntry component's reveal/hide
// logic is tested by verifying the initial masked state and the presence
// of the Reveal trigger.
// ---------------------------------------------------------------------------

describe("ContactChannelCard — secured entity_info entry (reveal/hide)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useAddEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useAddEntityContact>);
    vi.mocked(useDeleteEntityContact).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useDeleteEntityContact>);
    vi.mocked(useUpdateEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateEntityContact>);
    vi.mocked(useRevealEntityContactSecret).mockReturnValue({ mutate: vi.fn() } as unknown as ReturnType<typeof useRevealEntityContactSecret>);
  });

  it("renders the secured contact name in the collapsed row", () => {
    setLinkedContacts([SECURED_CONTACT]);
    const html = renderCard();
    expect(html).toContain("Diana Prince");
  });

  it("renders a masked placeholder chip for secured entries in collapsed view", () => {
    setLinkedContacts([SECURED_CONTACT]);
    const html = renderCard();
    // Collapsed view shows "••••" for secured entries
    expect(html).toContain("••••");
  });

  it("does NOT render the revealed-secret testid in the initial (masked) render", () => {
    setLinkedContacts([SECURED_CONTACT]);
    const html = renderCard();
    // Initial state: secret is masked, revealed-secret element not shown
    expect(html).not.toContain('data-testid="revealed-secret"');
  });

  it("does NOT render any raw secret value in the initial render (value is null)", () => {
    setLinkedContacts([SECURED_CONTACT]);
    const html = renderCard();
    // The secured entry has value: null — the masked placeholder should be shown,
    // not any actual secret value string.
    expect(html).toContain("••••");
  });

  it("renders the non-secured email entry alongside the secured entry in collapsed view", () => {
    setLinkedContacts([SECURED_CONTACT]);
    const html = renderCard();
    // The non-secured email chip should be visible
    expect(html).toContain("diana@example.com");
  });

  it("renders a contact row for the secured contact", () => {
    setLinkedContacts([SECURED_CONTACT]);
    const html = renderCard();
    expect(html).toContain('data-testid="contact-row-contact-004"');
  });
});

// ---------------------------------------------------------------------------
// Tests: zero linked contacts (empty-state)
// ---------------------------------------------------------------------------

describe("ContactChannelCard — zero linked contacts (empty-state)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useAddEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useAddEntityContact>);
    vi.mocked(useDeleteEntityContact).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useDeleteEntityContact>);
    vi.mocked(useUpdateEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateEntityContact>);
  });

  it("renders the empty-state section", () => {
    setLinkedContacts([]);
    const html = renderCard();
    expect(html).toContain('data-testid="contact-channel-empty-state"');
  });

  it("renders descriptive empty-state text", () => {
    setLinkedContacts([]);
    const html = renderCard();
    expect(html).toContain("No linked contacts");
  });

  it("renders the Link contact CTA when onLinkContact is provided", () => {
    setLinkedContacts([]);
    const onLink = vi.fn();
    const html = renderCard("entity-001", onLink);
    expect(html).toContain('data-testid="link-contact-cta"');
    expect(html).toContain("Link contact");
  });

  it("does NOT render Link contact CTA when onLinkContact is not provided", () => {
    setLinkedContacts([]);
    const html = renderCard("entity-001", undefined);
    expect(html).not.toContain('data-testid="link-contact-cta"');
  });

  it("does NOT render any contact rows when contacts list is empty", () => {
    setLinkedContacts([]);
    const html = renderCard();
    expect(html).not.toContain('data-testid="contact-row-');
  });

  it("still renders the Channels heading in empty state", () => {
    setLinkedContacts([]);
    const html = renderCard();
    expect(html).toContain("Channels");
    expect(html).toContain('data-testid="contact-channel-card"');
  });
});

// ---------------------------------------------------------------------------
// Tests: loading state
// ---------------------------------------------------------------------------

describe("ContactChannelCard — loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useAddEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useAddEntityContact>);
    vi.mocked(useDeleteEntityContact).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useDeleteEntityContact>);
    vi.mocked(useUpdateEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateEntityContact>);
  });

  it("renders the loading skeleton when isLoading is true", () => {
    setLinkedContacts([], { isLoading: true });
    const html = renderCard();
    expect(html).toContain("Channels");
    expect(html).toContain("animate-pulse");
  });
});

// ---------------------------------------------------------------------------
// Tests: ExpandedContactInfoRow — edit/delete affordances (bu-690xu)
//
// edit/delete affordances are present for entity_facts-sourced entries
// (source="entity_facts"). Legacy contact_info rows (source=null) remain
// read-only (shown with a "legacy" marker) because they are write-blocked.
//
// Edit button is now ENABLED for entity_facts rows and wired to
// useUpdateEntityContact (bu-690xu). Secured entries get a disabled Edit
// button since inline text editing of a masked value makes no sense.
//
// These tests render ExpandedContactInfoRow directly to verify the expanded
// row's actual structure (the collapsed card never renders ExpandedContactInfoRow).
// ---------------------------------------------------------------------------

describe("ExpandedContactInfoRow — entity_facts entries have Delete button", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useDeleteEntityContact).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useDeleteEntityContact>);
    vi.mocked(useUpdateEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateEntityContact>);
  });

  it("renders a Delete (Trash) button for an entity_facts entry", () => {
    const html = renderExpandedRow(CI_ENTITY_FACTS_TELEGRAM);
    expect(html).toContain('title="Delete"');
  });

  it("renders a Delete button for a phone entity_facts entry", () => {
    const html = renderExpandedRow(CI_PHONE_ENTITY_FACTS);
    expect(html).toContain('title="Delete"');
  });

  it("renders a Delete button for a website entity_facts entry", () => {
    const html = renderExpandedRow(CI_WEBSITE);
    expect(html).toContain('title="Delete"');
  });

  it("still renders the channel value alongside the Delete button", () => {
    const html = renderExpandedRow(CI_ENTITY_FACTS_TELEGRAM);
    expect(html).toContain("@alice_tg");
    expect(html).toContain('title="Delete"');
  });
});

describe("ExpandedContactInfoRow — entity_facts entries have Edit button (ENABLED, bu-690xu)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useDeleteEntityContact).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useDeleteEntityContact>);
    vi.mocked(useUpdateEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateEntityContact>);
  });

  it("renders an Edit (Pencil) button for an entity_facts entry (enabled — update endpoint exists)", () => {
    const html = renderExpandedRow(CI_ENTITY_FACTS_TELEGRAM);
    // Edit button is present and enabled (update-in-place endpoint exists as of bu-690xu)
    expect(html).toContain('title="Edit"');
    // Must NOT contain the old disabled tooltip
    expect(html).not.toContain('title="Edit (not yet supported for entity-facts channels)"');
  });

  it("renders Edit button with the edit testid", () => {
    const html = renderExpandedRow(CI_ENTITY_FACTS_TELEGRAM);
    expect(html).toContain('data-testid="edit-contact-btn"');
  });

  it("Edit button is not disabled for a non-secured entity_facts entry", () => {
    const html = renderExpandedRow(CI_ENTITY_FACTS_TELEGRAM);
    // The edit button should NOT have the disabled HTML attribute.
    // renderToStaticMarkup renders disabled as `disabled=""` when present.
    const editBtnIdx = html.indexOf('data-testid="edit-contact-btn"');
    expect(editBtnIdx).toBeGreaterThan(-1);
    // Extract the full opening button tag around the testid attribute.
    const btnStart = html.lastIndexOf("<button", editBtnIdx);
    const btnEnd = html.indexOf(">", editBtnIdx);
    const btnTag = html.slice(btnStart, btnEnd + 1);
    // The disabled attribute renders as 'disabled=""' — check for that specific form.
    expect(btnTag).not.toContain('disabled=""');
  });
});

describe("ExpandedContactInfoRow — legacy entries are read-only (source=null)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useDeleteEntityContact).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useDeleteEntityContact>);
    vi.mocked(useUpdateEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateEntityContact>);
  });

  it("does NOT render Edit or Delete buttons for a legacy (source=null) entry", () => {
    const html = renderExpandedRow(CI_LEGACY_EMAIL);
    expect(html).not.toContain('title="Delete"');
    expect(html).not.toContain('title="Edit"');
  });

  it("renders the legacy marker for source=null entries", () => {
    const html = renderExpandedRow(CI_LEGACY_EMAIL);
    expect(html).toContain("(legacy)");
  });

  it("still renders the channel value for legacy entries", () => {
    const html = renderExpandedRow(CI_LEGACY_EMAIL);
    expect(html).toContain("alice@example.com");
  });
});

describe("ExpandedContactInfoRow — delete mutation uses useDeleteEntityContact", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useUpdateEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateEntityContact>);
  });

  it("calls useDeleteEntityContact hook (not a contact-keyed hook)", () => {
    const mockDeleteMutate = vi.fn();
    vi.mocked(useDeleteEntityContact).mockReturnValue({
      mutate: mockDeleteMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteEntityContact>);

    // Render an entity_facts entry — the component should consume useDeleteEntityContact
    renderExpandedRow(CI_ENTITY_FACTS_TELEGRAM, "entity-001");

    // The hook must have been called (component setup)
    expect(vi.mocked(useDeleteEntityContact)).toHaveBeenCalled();
  });
});

describe("ExpandedContactInfoRow — edit mutation uses useUpdateEntityContact (bu-690xu)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useDeleteEntityContact).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useDeleteEntityContact>);
  });

  it("calls useUpdateEntityContact hook for entity_facts entries", () => {
    const mockUpdateMutateAsync = vi.fn();
    vi.mocked(useUpdateEntityContact).mockReturnValue({
      mutateAsync: mockUpdateMutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateEntityContact>);

    // Render a non-secured entity_facts entry
    renderExpandedRow(CI_ENTITY_FACTS_TELEGRAM, "entity-001");

    // The hook must have been called (component setup)
    expect(vi.mocked(useUpdateEntityContact)).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Tests: secured entity_facts reveal (entity-keyed path only)
//
// SecuredChannelEntry routes the reveal call to useRevealEntityContactSecret
// for all entries (source="entity_facts"). All entries from
// list_entity_linked_contacts carry source="entity_facts" since
// public.contact_info was dropped in bu-e2ja9.
//
// These tests verify the hook is called at component render time and that
// the masked placeholder is shown in the initial state.
// The actual reveal interaction (click → API call → reveal) requires a live
// DOM and is covered by ContactChannelCard.reveal.test.tsx.
// ---------------------------------------------------------------------------

describe("SecuredChannelEntry — masked initial state (entity_facts secured entry)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useDeleteEntityContact).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useDeleteEntityContact>);
    vi.mocked(useUpdateEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateEntityContact>);
    vi.mocked(useRevealEntityContactSecret).mockReturnValue({ mutate: vi.fn() } as unknown as ReturnType<typeof useRevealEntityContactSecret>);
  });

  it("renders the masked placeholder for a secured entity_facts entry", () => {
    const html = renderExpandedRow(CI_SECURED_ENTITY_FACTS, "entity-005");
    expect(html).toContain('data-testid="masked-secret"');
    expect(html).toContain("••••••••");
  });

  it("does NOT render the revealed-secret testid in the initial state", () => {
    const html = renderExpandedRow(CI_SECURED_ENTITY_FACTS, "entity-005");
    expect(html).not.toContain('data-testid="revealed-secret"');
  });

  it("renders the Reveal button for a secured entity_facts entry", () => {
    const html = renderExpandedRow(CI_SECURED_ENTITY_FACTS, "entity-005");
    expect(html).toContain("Reveal");
  });

  it("calls useRevealEntityContactSecret (entity-keyed hook) for entity_facts entries", () => {
    renderExpandedRow(CI_SECURED_ENTITY_FACTS, "entity-005");
    expect(vi.mocked(useRevealEntityContactSecret)).toHaveBeenCalled();
  });
});

describe("ContactChannelCard — secured entity_facts entry in collapsed view", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useAddEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useAddEntityContact>);
    vi.mocked(useDeleteEntityContact).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useDeleteEntityContact>);
    vi.mocked(useUpdateEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateEntityContact>);
    vi.mocked(useRevealEntityContactSecret).mockReturnValue({ mutate: vi.fn() } as unknown as ReturnType<typeof useRevealEntityContactSecret>);
  });

  it("renders the secured entity_facts contact name in the collapsed row", () => {
    setLinkedContacts([SECURED_ENTITY_FACTS_CONTACT]);
    const html = renderCard();
    expect(html).toContain("Eve Adams");
  });

  it("renders a masked placeholder chip for secured entity_facts entries in collapsed view", () => {
    setLinkedContacts([SECURED_ENTITY_FACTS_CONTACT]);
    const html = renderCard();
    expect(html).toContain("••••");
  });

  it("renders the non-secured email entry alongside the secured entity_facts entry", () => {
    setLinkedContacts([SECURED_ENTITY_FACTS_CONTACT]);
    const html = renderCard();
    expect(html).toContain("eve@example.com");
  });

  it("renders a contact row for the secured entity_facts contact", () => {
    setLinkedContacts([SECURED_ENTITY_FACTS_CONTACT]);
    const html = renderCard();
    expect(html).toContain('data-testid="contact-row-contact-005"');
  });
});

// ---------------------------------------------------------------------------
// Tests: sortChannelsPrimaryFirst — unit tests (bu-dvquo)
// ---------------------------------------------------------------------------

describe("sortChannelsPrimaryFirst — primary-first ordering", () => {
  const makeEntry = (id: string, isPrimary: boolean): ContactInfoEntry => ({
    id,
    type: "email",
    value: `${id}@example.com`,
    is_primary: isPrimary,
    secured: false,
    parent_id: null,
    context: null,
    source: "entity_facts",
    predicate: "has-email",
    value_hash: id,
  });

  it("returns empty array unchanged", () => {
    expect(sortChannelsPrimaryFirst([])).toEqual([]);
  });

  it("returns single entry unchanged", () => {
    const entry = makeEntry("a", false);
    expect(sortChannelsPrimaryFirst([entry])).toEqual([entry]);
  });

  it("places is_primary=true entry before is_primary=false entry", () => {
    const nonPrimary = makeEntry("a", false);
    const primary = makeEntry("b", true);
    const result = sortChannelsPrimaryFirst([nonPrimary, primary]);
    expect(result[0].id).toBe("b"); // primary first
    expect(result[1].id).toBe("a");
  });

  it("preserves relative order of non-primary entries (stable sort)", () => {
    const a = makeEntry("a", false);
    const b = makeEntry("b", false);
    const c = makeEntry("c", false);
    const result = sortChannelsPrimaryFirst([a, b, c]);
    expect(result.map((e) => e.id)).toEqual(["a", "b", "c"]);
  });

  it("preserves relative order of primary entries (stable sort)", () => {
    const p1 = makeEntry("p1", true);
    const p2 = makeEntry("p2", true);
    const result = sortChannelsPrimaryFirst([p1, p2]);
    expect(result.map((e) => e.id)).toEqual(["p1", "p2"]);
  });

  it("groups all primary entries before all non-primary entries", () => {
    const n1 = makeEntry("n1", false);
    const p1 = makeEntry("p1", true);
    const n2 = makeEntry("n2", false);
    const p2 = makeEntry("p2", true);
    const result = sortChannelsPrimaryFirst([n1, p1, n2, p2]);
    expect(result[0].is_primary).toBe(true);
    expect(result[1].is_primary).toBe(true);
    expect(result[2].is_primary).toBe(false);
    expect(result[3].is_primary).toBe(false);
  });

  it("does not mutate the input array", () => {
    const a = makeEntry("a", false);
    const b = makeEntry("b", true);
    const input = [a, b];
    const inputCopy = [...input];
    sortChannelsPrimaryFirst(input);
    expect(input).toEqual(inputCopy); // original untouched
  });
});

// ---------------------------------------------------------------------------
// Tests: ContactRow — primary-first rendering in collapsed + expanded views
// (bu-dvquo)
//
// The collapsed view renders nonSecuredChannels chips; the test verifies that
// the primary channel chip appears first by inspecting the order of channel
// values in the rendered HTML string.
//
// The expanded view (via ExpandedContactInfoRow) renders entries in primary-first
// order. Since renderToStaticMarkup renders the initial collapsed state, we
// test ordering through the chip badge text positions in the HTML.
// ---------------------------------------------------------------------------

// Contact with a non-primary email followed by a primary phone (insertion order
// is intentionally non-primary-first to assert reordering).
const CI_NON_PRIMARY_EMAIL: ContactInfoEntry = {
  id: "ci-ord-01",
  type: "email",
  value: "order-test@example.com",
  is_primary: false,
  secured: false,
  parent_id: null,
  context: null,
  source: "entity_facts",
  predicate: "has-email",
  value_hash: "ord01",
};

const CI_PRIMARY_PHONE: ContactInfoEntry = {
  id: "ci-ord-02",
  type: "phone",
  value: "555-ORDER",
  is_primary: true,
  secured: false,
  parent_id: null,
  context: null,
  source: "entity_facts",
  predicate: "has-phone",
  value_hash: "ord02",
};

const ORDERING_CONTACT: LinkedContactSummary = {
  id: "contact-ord",
  full_name: "Order Tester",
  email: null,
  phone: null,
  // insertion order: non-primary email first, primary phone second
  contact_info: [CI_NON_PRIMARY_EMAIL, CI_PRIMARY_PHONE],
  labels: [],
  preferred_channel: null,
  reachable_channels: [],
};

describe("ContactChannelCard — primary-first channel ordering (bu-dvquo)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useAddEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useAddEntityContact>);
    vi.mocked(useDeleteEntityContact).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useDeleteEntityContact>);
    vi.mocked(useUpdateEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateEntityContact>);
  });

  it("renders the primary phone chip before the non-primary email chip in collapsed view", () => {
    setLinkedContacts([ORDERING_CONTACT]);
    const html = renderCard();
    // Both values must appear
    expect(html).toContain("555-ORDER");
    expect(html).toContain("order-test@example.com");
    // Primary phone must appear BEFORE non-primary email in the HTML
    const phonePos = html.indexOf("555-ORDER");
    const emailPos = html.indexOf("order-test@example.com");
    expect(phonePos).toBeGreaterThan(-1);
    expect(emailPos).toBeGreaterThan(-1);
    expect(phonePos).toBeLessThan(emailPos);
  });

  it("never collapses multi-valued channels — all non-secured chips visible (up to display cap)", () => {
    setLinkedContacts([ORDERING_CONTACT]);
    const html = renderCard();
    // Both channels must be shown (only 2 entries; cap is 3)
    expect(html).toContain("555-ORDER");
    expect(html).toContain("order-test@example.com");
    // The "+X more" overflow badge must NOT appear for 2 entries
    expect(html).not.toContain("more");
  });
});

// ---------------------------------------------------------------------------
// Tests: amber unverified-dot (bu-e90i6)
//
// ExpandedContactInfoRow shows an amber dot (data-testid="unverified-dot") for
// entity_facts entries where verified !== true (default for all new channels).
// The dot is absent when verified=true. Legacy (source=null) entries never
// show the dot.
// ---------------------------------------------------------------------------

describe("ExpandedContactInfoRow — amber unverified-dot (bu-e90i6)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useDeleteEntityContact).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useDeleteEntityContact>);
    vi.mocked(useMarkEntityContactVerified).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useMarkEntityContactVerified>);
    vi.mocked(useUpdateEntityContact).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateEntityContact>);
  });

  it("renders amber dot when entry is entity_facts and verified is false", () => {
    const entry: ContactInfoEntry = {
      ...CI_ENTITY_FACTS_TELEGRAM,
      verified: false,
    };
    const html = renderExpandedRow(entry);
    expect(html).toContain('data-testid="unverified-dot"');
    expect(html).toContain("var(--amber)");
  });

  it("renders amber dot when entry is entity_facts and verified is absent (undefined)", () => {
    // verified is optional; absence is treated as unverified
    const entry: ContactInfoEntry = {
      ...CI_ENTITY_FACTS_TELEGRAM,
      // verified intentionally omitted (undefined)
    };
    const html = renderExpandedRow(entry);
    expect(html).toContain('data-testid="unverified-dot"');
    expect(html).toContain("var(--amber)");
  });

  it("does NOT render amber dot when entry is entity_facts and verified is true", () => {
    const entry: ContactInfoEntry = {
      ...CI_ENTITY_FACTS_TELEGRAM,
      verified: true,
    };
    const html = renderExpandedRow(entry);
    expect(html).not.toContain('data-testid="unverified-dot"');
  });

  it("does NOT render amber dot for legacy (source=null) entries", () => {
    const entry: ContactInfoEntry = {
      ...CI_LEGACY_EMAIL,
      // verified absent (undefined) — legacy entries never show the dot
    };
    const html = renderExpandedRow(entry);
    expect(html).not.toContain('data-testid="unverified-dot"');
  });

  it("renders mark-verified button for unverified entity_facts entry", () => {
    const entry: ContactInfoEntry = {
      ...CI_ENTITY_FACTS_TELEGRAM,
      verified: false,
    };
    const html = renderExpandedRow(entry);
    expect(html).toContain('data-testid="mark-verified-btn"');
  });

  it("does NOT render mark-verified button when entry is already verified", () => {
    const entry: ContactInfoEntry = {
      ...CI_ENTITY_FACTS_TELEGRAM,
      verified: true,
    };
    const html = renderExpandedRow(entry);
    expect(html).not.toContain('data-testid="mark-verified-btn"');
  });

  it("calls useMarkEntityContactVerified hook", () => {
    const entry: ContactInfoEntry = { ...CI_ENTITY_FACTS_TELEGRAM, verified: false };
    renderExpandedRow(entry, "entity-001");
    expect(vi.mocked(useMarkEntityContactVerified)).toHaveBeenCalled();
  });
});
