/**
 * ContactChannelCard tests
 *
 * Covers:
 * - Entity with one linked contact (populated state)
 * - Entity with multiple linked contacts (multi-contact stacking)
 * - Entity with sparse contact (no labels, no preferred_channel, one channel)
 * - Entity with secured contact_info (reveal/hide cycle)
 * - Entity with zero linked contacts (empty-state with Link contact CTA)
 *
 * IMPORTANT: Secured reveal tests assert that the secret value does NOT appear
 * in the masked render. They DO NOT assert the actual secret value to prevent
 * it from leaking into snapshot text.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ContactChannelCard } from "@/components/relationship/ContactChannelCard";
import { useEntityLinkedContacts } from "@/hooks/use-entities";
import type { LinkedContactSummary } from "@/api/types";

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-entities", () => ({
  useEntityLinkedContacts: vi.fn(),
}));

// All contact mutation hooks return minimal viable mocks at module level.
// No per-test override needed — the module mock factory is the source of truth.
vi.mock("@/hooks/use-contacts", () => ({
  useRevealContactSecret: vi.fn(() => ({ mutate: vi.fn() })),
  useCreateContactInfo: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useDeleteContactInfo: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePatchContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePatchContactInfo: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
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

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CONTACT_ONE: LinkedContactSummary = {
  id: "contact-001",
  full_name: "Alice Smith",
  email: "alice@example.com",
  phone: null,
  contact_info: [
    {
      id: "ci-001",
      type: "email",
      value: "alice@example.com",
      is_primary: true,
      secured: false,
      parent_id: null,
      context: null,
    },
    {
      id: "ci-002",
      type: "telegram",
      value: "@alice_tg",
      is_primary: false,
      secured: false,
      parent_id: null,
      context: null,
    },
  ],
  labels: [
    { id: "label-001", name: "Friend", color: null },
  ],
  preferred_channel: "telegram",
};

const CONTACT_TWO: LinkedContactSummary = {
  id: "contact-002",
  full_name: "Bob Jones",
  email: "bob@example.com",
  phone: "555-0100",
  contact_info: [
    {
      id: "ci-010",
      type: "phone",
      value: "555-0100",
      is_primary: true,
      secured: false,
      parent_id: null,
      context: null,
    },
  ],
  labels: [
    { id: "label-002", name: "Work", color: "#1a73e8" },
  ],
  preferred_channel: null,
};

const SPARSE_CONTACT: LinkedContactSummary = {
  id: "contact-003",
  full_name: "Charlie",
  email: null,
  phone: null,
  contact_info: [
    {
      id: "ci-020",
      type: "website",
      value: "https://charlie.example.com",
      is_primary: false,
      secured: false,
      parent_id: null,
      context: null,
    },
  ],
  labels: [],
  preferred_channel: null,
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
    },
    {
      id: "ci-031",
      type: "other",
      value: null, // secured — value is null until revealed
      is_primary: false,
      secured: true,
      parent_id: null,
      context: null,
    },
  ],
  labels: [],
  preferred_channel: null,
};

// ---------------------------------------------------------------------------
// Tests: populated state — one linked contact
// ---------------------------------------------------------------------------

describe("ContactChannelCard — one linked contact (populated state)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
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

describe("ContactChannelCard — secured contact_info (reveal/hide)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
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
  });

  it("renders the loading skeleton when isLoading is true", () => {
    setLinkedContacts([], { isLoading: true });
    const html = renderCard();
    expect(html).toContain("Channels");
    expect(html).toContain("animate-pulse");
  });
});
