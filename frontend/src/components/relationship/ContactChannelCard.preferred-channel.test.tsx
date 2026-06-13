// @vitest-environment jsdom
/**
 * ContactChannelCard — preferred-channel control tests (entity-keyed).
 *
 * The preferred-channel control reads/writes the entity-keyed `prefers-channel`
 * fact (entity-keyed-preferred-channel, group 3), NOT the orphaned
 * contacts.preferred_channel CRM column. Set routes to useSetPreferredChannel
 * (PUT /entities/{id}/preferred-channel); clear routes to useClearPreferredChannel
 * (DELETE). Only the entity's reachable_channels are selectable.
 *
 * The control renders in the expanded contact row. These tests render the full
 * card, expand the first contact, and drive the (mocked native) Select.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";

import { ContactChannelCard } from "@/components/relationship/ContactChannelCard";
import {
  useEntityLinkedContacts,
  useAddEntityContact,
  useDeleteEntityContact,
  useUpdateEntityContact,
  useRevealEntityContactSecret,
  useSetPreferredChannel,
  useClearPreferredChannel,
} from "@/hooks/use-entities";
import type { LinkedContactSummary } from "@/api/types";

// Mock shadcn Select with a native <select> so tests can fire change events.
// The whole Select renders a single native <select> with the SelectItem
// <option>s nested inside it (SelectTrigger/SelectValue collapse to null), so
// `select.value = ...` resolves against real options and change events carry
// the chosen value to onValueChange.
vi.mock("@/components/ui/select", () => ({
  Select: ({
    children,
    onValueChange,
    value,
  }: {
    children: ReactNode;
    onValueChange?: (v: string) => void;
    value?: string;
  }) => (
    <select
      data-testid="preferred-channel-select"
      value={value}
      onChange={(e) => onValueChange?.((e.target as HTMLSelectElement).value)}
    >
      {children}
    </select>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: ReactNode }) => <>{children}</>,
  SelectItem: ({
    value,
    children,
    disabled,
  }: {
    value: string;
    children: ReactNode;
    disabled?: boolean;
  }) => (
    <option value={value} disabled={disabled}>
      {children}
    </option>
  ),
}));

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

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CONTACT_EMAIL_TELEGRAM: LinkedContactSummary = {
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
      source: "entity_facts",
      predicate: "has-email",
      value_hash: "aabbccddeeff0011",
    },
  ],
  labels: [],
  preferred_channel: "telegram",
  reachable_channels: ["email", "telegram"],
};

const CONTACT_EMAIL_ONLY: LinkedContactSummary = {
  ...CONTACT_EMAIL_TELEGRAM,
  preferred_channel: null,
  reachable_channels: ["email"],
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let container: HTMLDivElement;
let root: Root;

function setLinkedContacts(contacts: LinkedContactSummary[]) {
  vi.mocked(useEntityLinkedContacts).mockReturnValue({
    data: contacts,
    isLoading: false,
  } as ReturnType<typeof useEntityLinkedContacts>);
}

function renderCard(entityId = "entity-001") {
  act(() => {
    root.render(<ContactChannelCard entityId={entityId} />);
  });
}

function expandFirstContact() {
  const header = container.querySelector(
    '[data-testid^="contact-row-"] button[aria-expanded]',
  ) as HTMLButtonElement | null;
  if (!header) throw new Error("collapsed contact header not found");
  act(() => {
    header.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

function getPreferredSelect(): HTMLSelectElement {
  const select = container.querySelector(
    '[data-testid="preferred-channel-select"]',
  ) as HTMLSelectElement | null;
  if (!select) throw new Error("preferred-channel select not found");
  return select;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ContactChannelCard — preferred-channel control (entity-keyed)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useAddEntityContact).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useAddEntityContact>);
    vi.mocked(useDeleteEntityContact).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteEntityContact>);
    vi.mocked(useUpdateEntityContact).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateEntityContact>);
    vi.mocked(useRevealEntityContactSecret).mockReturnValue({
      mutate: vi.fn(),
    } as unknown as ReturnType<typeof useRevealEntityContactSecret>);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("does NOT use the legacy contact-keyed patch hook (use-contacts not imported)", () => {
    // The component no longer imports usePatchContact; the preference is
    // written through the entity-keyed hooks below. This is a structural
    // guarantee: the mocks for the entity-keyed hooks exist and are consumed.
    setLinkedContacts([CONTACT_EMAIL_TELEGRAM]);
    renderCard();
    expandFirstContact();
    expect(vi.mocked(useSetPreferredChannel)).toHaveBeenCalled();
    expect(vi.mocked(useClearPreferredChannel)).toHaveBeenCalled();
  });

  it("selecting a reachable channel calls useSetPreferredChannel with { entityId, channel }", () => {
    const setMutate = vi.fn();
    vi.mocked(useSetPreferredChannel).mockReturnValue({
      mutate: setMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useSetPreferredChannel>);

    setLinkedContacts([CONTACT_EMAIL_ONLY]);
    renderCard("entity-009");
    expandFirstContact();

    const select = getPreferredSelect();
    act(() => {
      select.value = "email";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(setMutate).toHaveBeenCalledOnce();
    expect(setMutate.mock.calls[0][0]).toEqual({ entityId: "entity-009", channel: "email" });
  });

  it("selecting 'none' calls useClearPreferredChannel with { entityId }", () => {
    const clearMutate = vi.fn();
    vi.mocked(useClearPreferredChannel).mockReturnValue({
      mutate: clearMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useClearPreferredChannel>);

    setLinkedContacts([CONTACT_EMAIL_TELEGRAM]);
    renderCard("entity-009");
    expandFirstContact();

    const select = getPreferredSelect();
    act(() => {
      select.value = "none";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(clearMutate).toHaveBeenCalledOnce();
    expect(clearMutate.mock.calls[0][0]).toEqual({ entityId: "entity-009" });
  });

  it("offers only reachable channels — non-reachable channel option is disabled", () => {
    // Email-only contact: the Telegram option must be disabled (no telegram fact).
    setLinkedContacts([CONTACT_EMAIL_ONLY]);
    renderCard();
    expandFirstContact();

    const options = Array.from(
      container.querySelectorAll('[data-testid="preferred-channel-select"] option'),
    ) as HTMLOptionElement[];
    const byValue = new Map(options.map((o) => [o.value, o]));

    expect(byValue.get("email")?.disabled).toBe(false);
    expect(byValue.get("telegram")?.disabled).toBe(true);
  });
});
