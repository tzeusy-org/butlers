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
  email: null,
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
// mailto: / tel: link rendering — contact_info entries
// ---------------------------------------------------------------------------

describe("ContactDetailView — email/phone link rendering", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders email contact_info as mailto: anchor", () => {
    setContactState({
      ...BASE_CONTACT,
      contact_info: [
        {
          id: "ci-email",
          type: "email",
          value: "alice@example.com",
          is_primary: true,
          secured: false,
          parent_id: null,
        },
      ],
    });
    const html = renderPage();
    expect(html).toContain('href="mailto:alice@example.com"');
    expect(html).toContain("alice@example.com");
  });

  it("renders phone contact_info as tel: anchor with sanitized href", () => {
    setContactState({
      ...BASE_CONTACT,
      contact_info: [
        {
          id: "ci-phone",
          type: "phone",
          value: "+1 (415) 555-0100",
          is_primary: true,
          secured: false,
          parent_id: null,
        },
      ],
    });
    const html = renderPage();
    // Spaces and parentheses stripped; + and digits and hyphens preserved
    // "+1 (415) 555-0100" → "+1415555-0100" (spaces and parens removed, hyphen kept)
    expect(html).toContain('href="tel:+1415555-0100"');
    // Display text is the original formatted string
    expect(html).toContain("+1 (415) 555-0100");
  });

  it("does NOT render secured-masked rows as anchors", () => {
    setContactState({
      ...BASE_CONTACT,
      contact_info: [
        {
          id: "ci-secured",
          type: "email",
          value: null,   // null value = masked (secured)
          is_primary: false,
          secured: true,
          parent_id: null,
        },
      ],
    });
    const html = renderPage();
    // No mailto: link should be present
    expect(html).not.toContain("mailto:");
    // The masked placeholder should be visible
    expect(html).toContain("••••••••");
  });

  it("renders legacy contact.email as mailto: anchor", () => {
    setContactState({
      ...BASE_CONTACT,
      email: "legacy@example.com",
      contact_info: [],
    });
    const html = renderPage();
    expect(html).toContain('href="mailto:legacy@example.com"');
    expect(html).toContain("legacy@example.com");
  });

  it("renders legacy contact.phone as tel: anchor with sanitized href", () => {
    setContactState({
      ...BASE_CONTACT,
      phone: "(800) 555-1234",
      contact_info: [],
    });
    const html = renderPage();
    // Parentheses and spaces stripped; hyphens preserved
    // "(800) 555-1234" → "800555-1234"
    expect(html).toContain('href="tel:800555-1234"');
    // Display text unchanged
    expect(html).toContain("(800) 555-1234");
  });

  it("does NOT render non-email/phone contact_info as anchors", () => {
    setContactState({
      ...BASE_CONTACT,
      contact_info: [
        {
          id: "ci-tg",
          type: "telegram",
          value: "@alicebot",
          is_primary: false,
          secured: false,
          parent_id: null,
        },
      ],
    });
    const html = renderPage();
    // No mailto: or tel: for telegram
    expect(html).not.toContain("mailto:");
    expect(html).not.toContain("tel:");
    expect(html).toContain("@alicebot");
  });
});
