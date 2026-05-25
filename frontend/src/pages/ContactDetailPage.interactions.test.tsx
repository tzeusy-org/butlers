// @vitest-environment jsdom
/**
 * ContactDetailPage — interactive reveal tests.
 *
 * Uses @testing-library/react + fireEvent to exercise the click interaction
 * on the "Reveal" button for secured contact_info entries.
 * This complements the static-markup coverage in ContactDetailPage.test.tsx.
 *
 * Bead: bu-6dyn5
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ContactDetailPage from "@/pages/ContactDetailPage";
import { useContact, useRevealContactSecret } from "@/hooks/use-contacts";
import type { ContactDetail } from "@/api/types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ContactDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

// ---------------------------------------------------------------------------
// Secured credential reveal — click interaction [bu-6dyn5]
// ---------------------------------------------------------------------------

describe("ContactDetailPage — secured credential reveal on click", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("clicking Reveal calls useRevealContactSecret.mutate with the correct ids", () => {
    const mockMutate = vi.fn();
    vi.mocked(useRevealContactSecret).mockReturnValue({ mutate: mockMutate } as unknown as ReturnType<typeof useRevealContactSecret>);

    setContactState({
      ...BASE_CONTACT,
      contact_info: [
        {
          id: "ci-secret-1",
          type: "other",
          value: null,
          is_primary: false,
          secured: true,
          parent_id: null,
          context: null,
        },
      ],
    });

    renderPage();

    const revealButton = screen.getByRole("button", { name: /reveal/i });
    fireEvent.click(revealButton);

    expect(mockMutate).toHaveBeenCalledTimes(1);
    expect(mockMutate).toHaveBeenCalledWith(
      { contactId: "contact-001", infoId: "ci-secret-1" },
      expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
    );
  });

  it("Reveal button is initially enabled (not in revealing-in-progress state)", () => {
    // The button's disabled attribute is set to isRevealing (false initially).
    // We verify the initial state: enabled, text says "Reveal" not "Revealing...".
    const mockMutate = vi.fn();
    vi.mocked(useRevealContactSecret).mockReturnValue({ mutate: mockMutate } as unknown as ReturnType<typeof useRevealContactSecret>);

    setContactState({
      ...BASE_CONTACT,
      contact_info: [
        {
          id: "ci-secret-1",
          type: "other",
          value: null,
          is_primary: false,
          secured: true,
          parent_id: null,
          context: null,
        },
      ],
    });

    renderPage();

    // The button renders with "Reveal" text (not "Revealing...")
    expect(screen.getByRole("button", { name: "Reveal" })).toBeTruthy();
    expect(screen.queryByText("Revealing...")).toBeNull();
  });

  it("Reveal button is rendered even when secured entry already has a value (allow re-reveal)", () => {
    // When secured=true and entry.value is non-null, the component still renders
    // the Reveal button because local `revealed` state starts as null.
    // The value is shown alongside the button (not masked).
    vi.mocked(useRevealContactSecret).mockReturnValue({ mutate: vi.fn() } as unknown as ReturnType<typeof useRevealContactSecret>);

    setContactState({
      ...BASE_CONTACT,
      contact_info: [
        {
          id: "ci-secret-1",
          type: "other",
          value: "already-revealed-value",
          is_primary: false,
          secured: true,
          parent_id: null,
          context: null,
        },
      ],
    });

    renderPage();

    // The value is shown (no masked dots) and Reveal button is still present
    expect(screen.queryByText("••••••••")).toBeNull();
    expect(screen.getByRole("button", { name: "Reveal" })).toBeTruthy();
  });
});
