// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { EntityDetail } from "@/api/types";

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

describe("OwnerSetupBanner", () => {
  it("keeps Telegram API hash entry exclusive to the guided session setup", () => {
    render(<OwnerSetupBanner entity={OWNER} />);

    fireEvent.click(screen.getByRole("button", { name: "Set up identity" }));
    fireEvent.click(screen.getByRole("button", { name: /Credentials \(optional\)/ }));

    expect(screen.queryByLabelText("Telegram API hash")).toBeNull();
    expect(screen.getByText(/Telegram user session is generated interactively/i)).toBeTruthy();
  });
});
