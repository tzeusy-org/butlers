// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EntityDetail, LinkedContactSummary } from "@/api/types";

vi.mock("@/hooks/use-entities", () => ({
  useAddEntityContact: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useEntityLinkedContacts: vi.fn(() => ({ data: [], isLoading: false })),
}));

vi.mock("@/hooks/use-memory", () => ({
  useCreateEntityInfo: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useUpdateEntity: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
}));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import { OwnerSetupBanner } from "./OwnerSetupBanner";
import { useEntityLinkedContacts } from "@/hooks/use-entities";

const OWNER: EntityDetail = {
  id: "owner-entity",
  canonical_name: "Owner",
  entity_type: "person",
  aliases: [],
  roles: ["owner"],
  fact_count: 0,
  linked_contact_id: null,
  linked_contact_name: null,
  unidentified: false,
  source_butler: null,
  source_scope: null,
  created_at: "2026-07-17T00:00:00Z",
  updated_at: "2026-07-17T00:00:00Z",
  dunbar_tier: null,
  dunbar_score: null,
  archived: false,
  metadata: {},
  recent_facts: [],
  recent_facts_total: 0,
  recent_facts_offset: 0,
  recent_facts_limit: 20,
  recent_facts_has_more: false,
  entity_info: [],
};

const CONFIGURED_OWNER: EntityDetail = {
  ...OWNER,
  canonical_name: "Tze",
};

const TELEGRAM_CONTACT: LinkedContactSummary = {
  id: "contact-1",
  full_name: "Tze",
  email: null,
  phone: null,
  contact_info: [
    {
      id: "handle-1",
      type: "telegram_user_id",
      value: "tze",
      is_primary: true,
      secured: false,
      parent_id: null,
      context: null,
      source: "entity_facts",
      predicate: "has-handle",
      value_hash: "handle-hash",
      verified: true,
    },
    {
      id: "chat-id-1",
      type: "telegram_user_id",
      value: "123456789",
      is_primary: true,
      secured: false,
      parent_id: null,
      context: null,
      source: "entity_facts",
      predicate: "has-handle",
      value_hash: "chat-id-hash",
      verified: true,
    },
  ],
  labels: [],
  preferred_channel: null,
  reachable_channels: ["telegram"],
};

function mockLinkedContacts(options: {
  data?: LinkedContactSummary[];
  isLoading?: boolean;
  isError?: boolean;
  refetch?: ReturnType<typeof vi.fn>;
} = {}) {
  const data = "data" in options ? options.data : [];
  const isLoading = options.isLoading ?? false;
  const isError = options.isError ?? false;
  const refetch = options.refetch ?? vi.fn();

  vi.mocked(useEntityLinkedContacts).mockReturnValue({
    data,
    isLoading,
    isError,
    refetch,
  } as unknown as ReturnType<typeof useEntityLinkedContacts>);

  return refetch;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("OwnerSetupBanner", () => {
  it("keeps Telegram API hash entry exclusive to the guided session setup", () => {
    mockLinkedContacts();
    render(<OwnerSetupBanner entity={OWNER} />);

    fireEvent.click(screen.getByRole("button", { name: "Set up identity" }));
    fireEvent.click(screen.getByRole("button", { name: /Credentials \(optional\)/ }));

    expect(screen.queryByLabelText("Telegram API hash")).toBeNull();
    expect(screen.getByText(/Telegram user session is generated interactively/i)).toBeTruthy();
  });

  it("does not claim the owner identity is incomplete while linked contacts are initially loading", () => {
    mockLinkedContacts({ data: undefined, isLoading: true });

    render(<OwnerSetupBanner entity={OWNER} />);

    expect(screen.queryByText("Owner identity incomplete")).toBeNull();
    expect(screen.queryByRole("button", { name: "Set up identity" })).toBeNull();
  });

  it("renders a retryable degraded note instead of a setup CTA when linked contacts initially error", () => {
    const refetch = mockLinkedContacts({ data: undefined, isError: true });

    render(<OwnerSetupBanner entity={OWNER} />);

    expect(screen.getByRole("alert").textContent).toContain("Owner contact facts: unavailable");
    expect(screen.queryByRole("button", { name: "Set up identity" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("keeps the setup CTA for a confirmed successful empty linked-contact response", () => {
    mockLinkedContacts({ data: [] });

    render(<OwnerSetupBanner entity={OWNER} />);

    expect(screen.getByText("Owner identity incomplete")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Set up identity" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps cached incomplete identity facts actionable while disclosing a failed refresh", () => {
    const refetch = mockLinkedContacts({ data: [], isError: true });

    render(<OwnerSetupBanner entity={OWNER} />);

    expect(screen.getByText("Owner identity incomplete")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Set up identity" })).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("Owner contact facts: unavailable");

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("uses cached contact facts during a failed refresh and discloses the degraded source", () => {
    const refetch = mockLinkedContacts({ data: [TELEGRAM_CONTACT], isError: true });

    render(<OwnerSetupBanner entity={CONFIGURED_OWNER} />);

    expect(screen.queryByText("Owner identity incomplete")).toBeNull();
    expect(screen.queryByRole("button", { name: "Set up identity" })).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("Owner contact facts: unavailable");

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledOnce();
  });
});
