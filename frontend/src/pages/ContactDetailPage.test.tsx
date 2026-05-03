import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ContactDetailPage from "@/pages/ContactDetailPage";
import { useContact } from "@/hooks/use-contacts";
import type { ContactDetail } from "@/api/types";

// Mock react-router's useParams so we can control the contactId
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useParams: vi.fn(() => ({ contactId: "contact-001" })) };
});

vi.mock("@/hooks/use-contacts", () => ({
  useContact: vi.fn(),
  useContacts: vi.fn(() => ({ data: { contacts: [] } })),
  useDeleteContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePatchContact: vi.fn(() => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false })),
  useCreateContactInfo: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useDeleteContactInfo: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePatchContactInfo: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useRevealContactSecret: vi.fn(() => ({ mutate: vi.fn() })),
}));

vi.mock("@/hooks/use-memory", () => ({
  useUnlinkContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

// Hooks used by PulseStrip (only rendered when contact has entity_id)
vi.mock("@/hooks/use-entities", () => ({
  useEntityTimeline: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityGifts: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityLoans: vi.fn(() => ({ data: [], isLoading: false })),
  useUpdateEntityDunbarTier: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

type UseContactResult = ReturnType<typeof useContact>;

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

function setContactState(contact: ContactDetail | null, opts: Partial<UseContactResult> = {}) {
  vi.mocked(useContact).mockReturnValue({
    data: contact ?? undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseContactResult);
}

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ContactDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Single-H1 contract — ContactDetailPage
// ---------------------------------------------------------------------------

describe("ContactDetailPage — single-H1 contract", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders exactly one <h1> element", () => {
    setContactState(BASE_CONTACT);
    const html = renderPage();
    const h1Matches = html.match(/<h1[\s>]/g) ?? [];
    expect(h1Matches).toHaveLength(1);
  });

  it("h1 contains the contact's full name", () => {
    setContactState({ ...BASE_CONTACT, full_name: "Bob Smith" });
    const html = renderPage();
    // Grab everything inside the h1
    const h1Match = html.match(/<h1[^>]*>(.*?)<\/h1>/s);
    expect(h1Match).not.toBeNull();
    expect(h1Match![1]).toContain("Bob Smith");
  });

  it("renders loading state without any <h1>", () => {
    setContactState(null, { isLoading: true });
    const html = renderPage();
    const h1Matches = html.match(/<h1[\s>]/g) ?? [];
    // Page archetype=detail shows HeadingBlockSkeleton (no h1) when loading
    expect(h1Matches).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Token-leak check — no raw hex color values [bu-rqfil.1]
// ---------------------------------------------------------------------------

describe("ContactDetailPage — token-leak guard", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders no inline hex color values in output", () => {
    setContactState({
      ...BASE_CONTACT,
      roles: ["owner", "admin"],
      contact_info: [
        {
          id: "ci-1",
          type: "email",
          value: "alice@example.com",
          is_primary: true,
          secured: false,
          parent_id: null,
        },
      ],
    });
    const html = renderPage();
    // Hex color leak pattern: inline style="...#RRGGBB..." or backgroundColor: "#..."
    // CSS var() tokens are allowed; raw hex in style attributes are not.
    expect(html).not.toMatch(/style="[^"]*#[0-9a-fA-F]{3,6}[^"]*"/);
  });
});

// ---------------------------------------------------------------------------
// Basic rendering
// ---------------------------------------------------------------------------

describe("ContactDetailPage — rendering", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders contact name in page heading", () => {
    setContactState(BASE_CONTACT);
    const html = renderPage();
    expect(html).toContain("Alice Example");
  });

  it("renders subtitle from email when contact_info is empty", () => {
    setContactState({ ...BASE_CONTACT, email: "test@example.com", contact_info: [] });
    const html = renderPage();
    expect(html).toContain("test@example.com");
  });

  it("falls back to legacy email when contact_info has no usable email/telegram", () => {
    setContactState({
      ...BASE_CONTACT,
      email: "fallback@example.com",
      contact_info: [
        { id: "ci-addr", type: "address", value: "123 Main St", is_primary: true, secured: false, parent_id: null },
      ],
    });
    const html = renderPage();
    expect(html).toContain("fallback@example.com");
  });

  it("does NOT render PulseStrip when contact has no entity_id", () => {
    setContactState({ ...BASE_CONTACT, entity_id: null });
    const html = renderPage();
    // PulseStrip renders Dunbar tier tiles; no entity means no strip
    expect(html).not.toContain("Dunbar tier");
  });

  it("renders ContactDetailView content", () => {
    setContactState(BASE_CONTACT);
    const html = renderPage();
    // ContactDetailView renders the contact info section
    expect(html).toContain("Alice Example");
  });
});
