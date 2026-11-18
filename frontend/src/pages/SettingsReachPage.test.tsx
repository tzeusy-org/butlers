/**
 * SettingsReachPage — /settings/reach
 *
 * The owner's list of break-through clauses: matchers in words, scope, expiry, and revoke behind
 * a confirm. Granting is not offered here.
 */

// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { axe, toHaveNoViolations } from "jest-axe";

import SettingsReachPage from "@/pages/SettingsReachPage";
import { describeClause } from "@/lib/reach-clause";
import type { ReachClause, ReachProposal } from "@/api/reach";

expect.extend(toHaveNoViolations);

function clause(over: Partial<ReachClause>): ReachClause {
  return {
    id: "c-1",
    entity_id: "e-1",
    entity_name: "Mum",
    sender_domain: null,
    channel: null,
    security_kind: null,
    scope: "always",
    expires_at: null,
    created_at: "2026-11-01T00:00:00Z",
    ...over,
  };
}

const hooks = vi.hoisted(() => ({
  useReachClauses: vi.fn(),
  useRevokeReachClause: vi.fn(),
  useReachProposals: vi.fn(),
  useConfirmReachProposal: vi.fn(),
  useDismissReachProposal: vi.fn(),
}));
vi.mock("@/hooks/use-reach-clauses", () => hooks);

const revoke = vi.fn();
const confirmProposal = vi.fn();
const dismissProposal = vi.fn();

function proposal(over: Partial<ReachProposal> = {}): ReachProposal {
  return {
    id: "p-1",
    sender_domain: "bank.example",
    channel: "email",
    security_kind: null,
    scope: "always",
    created_at: "2026-11-01T00:00:00Z",
    expires_at: "2099-11-02T00:00:00Z",
    ...over,
  };
}

function proposals(rows: ReachProposal[]) {
  hooks.useReachProposals.mockReturnValue({
    data: { data: { proposals: rows } },
    isLoading: false,
    isError: false,
  });
}

function listing(rows: ReachClause[]) {
  hooks.useReachClauses.mockReturnValue({
    data: { data: { clauses: rows } },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
}

function view() {
  return render(
    <MemoryRouter>
      <SettingsReachPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  revoke.mockReset();
  confirmProposal.mockReset();
  dismissProposal.mockReset();
  proposals([]);
  hooks.useConfirmReachProposal.mockReturnValue({ mutate: confirmProposal, isPending: false });
  hooks.useDismissReachProposal.mockReturnValue({ mutate: dismissProposal, isPending: false });
  hooks.useRevokeReachClause.mockReturnValue({ mutate: revoke, isPending: false });
});

afterEach(cleanup);

describe("describeClause", () => {
  it("names a person, a domain, a security alert or a channel in words", () => {
    expect(describeClause(clause({}))).toBe("Mum");
    expect(describeClause(clause({ entity_id: "e-2", entity_name: undefined }))).toBe(
      "a person you named",
    );
    expect(describeClause(clause({ entity_id: null, sender_domain: "bank.example" }))).toBe(
      "anyone at bank.example",
    );
    expect(describeClause(clause({ entity_id: null, security_kind: "new_sign_in" }))).toBe(
      "account alert: new sign-in",
    );
    expect(describeClause(clause({ entity_id: null, channel: "telegram" }))).toBe(
      "anything on Telegram",
    );
    expect(describeClause(clause({ channel: "email" }))).toBe("Mum, on email");
  });
});

describe("SettingsReachPage", () => {
  it("lists each clause with its scope and expiry", () => {
    listing([
      clause({}),
      clause({ id: "c-2", entity_id: null, sender_domain: "bank.example", scope: "quiet_hours_only" }),
    ]);
    view();
    const first = screen.getByRole("row", { name: /Mum/ });
    expect(within(first).getByText("Always")).toBeTruthy();
    expect(within(first).getByText("no expiry")).toBeTruthy();
    const second = screen.getByRole("row", { name: /bank\.example/ });
    expect(within(second).getByText("Only during quiet hours")).toBeTruthy();
  });

  it("revokes only after the owner confirms", () => {
    listing([clause({})]);
    view();
    fireEvent.click(screen.getByRole("button", { name: "Revoke Mum" }));
    expect(revoke).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("revoke-reach-dialog-confirm"));
    expect(revoke.mock.calls[0][0]).toBe("c-1");
  });

  it("closes the confirm dialog when the revoke fails", () => {
    listing([clause({})]);
    view();
    fireEvent.click(screen.getByRole("button", { name: "Revoke Mum" }));
    fireEvent.click(screen.getByTestId("revoke-reach-dialog-confirm"));
    act(() => revoke.mock.calls[0][1].onError());
    expect(screen.queryByTestId("revoke-reach-dialog-confirm")).toBeNull();
  });

  it("shows pending Telegram proposals with Confirm and Dismiss", () => {
    listing([]);
    proposals([proposal()]);
    view();
    expect(screen.getByText("Proposed from Telegram")).toBeTruthy();
    const row = screen.getByRole("row", { name: /anyone at bank\.example, on email/ });
    expect(within(row).getByText("Always")).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Confirm anyone at bank\.example/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Dismiss anyone at bank\.example/ })).toBeTruthy();
  });

  it("shows no proposal section when nothing is pending or the list fails", () => {
    listing([]);
    view();
    expect(screen.queryByText("Proposed from Telegram")).toBeNull();
    cleanup();
    hooks.useReachProposals.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(),
    });
    view();
    expect(screen.queryByText("Proposed from Telegram")).toBeNull();
    expect(screen.getByText(/Could not load proposals from Telegram/i)).toBeTruthy();
  });

  it("confirms a proposal only after the owner confirms in the dialog", () => {
    listing([]);
    proposals([proposal()]);
    view();
    fireEvent.click(screen.getByRole("button", { name: /^Confirm anyone at bank\.example/ }));
    expect(confirmProposal).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("confirm-reach-dialog-confirm"));
    expect(confirmProposal.mock.calls[0][0]).toBe("p-1");
  });

  it("closes the confirm dialog when the confirm fails, for example after expiry", () => {
    listing([]);
    proposals([proposal()]);
    view();
    fireEvent.click(screen.getByRole("button", { name: /^Confirm anyone at bank\.example/ }));
    fireEvent.click(screen.getByTestId("confirm-reach-dialog-confirm"));
    act(() => confirmProposal.mock.calls[0][1].onError());
    expect(screen.queryByTestId("confirm-reach-dialog-confirm")).toBeNull();
  });

  it("dismisses a proposal without a dialog", () => {
    listing([]);
    proposals([proposal()]);
    view();
    fireEvent.click(screen.getByRole("button", { name: /^Dismiss anyone at bank\.example/ }));
    expect(dismissProposal.mock.calls[0][0]).toBe("p-1");
  });

  it("offers no way to add a clause", () => {
    listing([clause({})]);
    view();
    expect(screen.queryByRole("button", { name: /add|new|create/i })).toBeNull();
  });

  it("says so when no one is declared, and when the list fails", () => {
    listing([]);
    const { unmount } = view();
    expect(screen.getByText(/No one is declared yet/i)).toBeTruthy();
    unmount();
    hooks.useReachClauses.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(),
    });
    view();
    expect(screen.getByText(/Could not load your reach list/i)).toBeTruthy();
  });

  it("has no axe violations", async () => {
    listing([clause({}), clause({ id: "c-2", entity_id: null, channel: "email" })]);
    proposals([proposal()]);
    const { container } = view();
    expect(await axe(container)).toHaveNoViolations();
  });
});
