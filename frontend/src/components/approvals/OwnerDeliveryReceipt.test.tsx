// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { acknowledgeOwnerDelivery, getOwnerDeliveryWhy, getPersistedOwnerDeliveryWhy } from "@/api/client";
import { OwnerDeliveryReceipt } from "./OwnerDeliveryReceipt";
import type { OwnerDeliveryWhy } from "@/api/types";

vi.mock("@/api/client", () => ({ acknowledgeOwnerDelivery: vi.fn(), getOwnerDeliveryWhy: vi.fn(),
  getPersistedOwnerDeliveryWhy: vi.fn() }));

const CARD = { subject_id: "subject", presentation_id: "presentation", generation: 1,
  approve_token: "approve", reject_token: "reject", ack_token: "ack" };
const WHY: OwnerDeliveryWhy = { subject_id: "subject", presentation_id: "presentation", generation: 1,
  current: true, kind: "approval", state: "delivered", decision: null, surface: "dashboard",
  delivery_state: "sent", urgency_class: "routine", urgency_reason: null, cause: "desk_first",
  deadline: null, evidence: [{ source: "dashboard_focus", surface: "dashboard", age_seconds: 94, state: "stale" }],
  holding_reason: null, effect_ref: null, effect_status: "pending", budget_status: "unknown", origin: "unknown",
  presentations: [{ surface: "dashboard", generation: 1, state: "sent" }] };

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><OwnerDeliveryReceipt card={CARD} presentationId="presentation" /></QueryClientProvider>);
  return within(screen.getByRole("region", { name: "Delivery receipt" }));
}
function openReceipt() {
  const details = screen.getByText("Why this delivery?").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
}

describe("canonical delivery receipt", () => {
  beforeEach(() => vi.resetAllMocks());
  afterEach(cleanup);

  it("loads evidence only on disclosure, with stale age and explicit unknowns", async () => {
    vi.mocked(getOwnerDeliveryWhy).mockResolvedValue({ data: WHY, meta: {} });
    const pane = mount();
    expect(getOwnerDeliveryWhy).not.toHaveBeenCalled();
    openReceipt();
    await waitFor(() => expect(pane.getByText("desk first")).not.toBeNull());
    expect(pane.getByText("dashboard focus: stale, 94 seconds old")).not.toBeNull();
    expect(pane.getAllByText("unknown")).toHaveLength(3);
    expect(acknowledgeOwnerDelivery).not.toHaveBeenCalled();
  });

  it("shows stale-token failure without fabricating a receipt or sending a decision", async () => {
    vi.mocked(getOwnerDeliveryWhy).mockRejectedValue(new Error("stale_generation"));
    const pane = mount();
    openReceipt();
    expect((await pane.findByRole("alert")).textContent).toContain("Evidence unavailable or this card is stale");
    expect(pane.queryByText("desk first")).toBeNull();
    expect(acknowledgeOwnerDelivery).not.toHaveBeenCalled();
  });

  it("acknowledges once through the existing action token and invalidates dossiers", async () => {
    vi.mocked(acknowledgeOwnerDelivery).mockResolvedValue({ data: { status: "acknowledged" }, meta: {} });
    const pane = mount();
    fireEvent.click(pane.getByRole("button", { name: "Acknowledge receipt" }));
    await waitFor(() => expect((pane.getByRole("button", { name: "Acknowledged" }) as HTMLButtonElement).disabled).toBe(true));
    expect(acknowledgeOwnerDelivery).toHaveBeenCalledExactlyOnceWith("ack");
    expect(getOwnerDeliveryWhy).not.toHaveBeenCalled();
  });

  it("keeps resolved presentation history readable without offering another action", async () => {
    vi.mocked(getPersistedOwnerDeliveryWhy).mockResolvedValue({
      data: { ...WHY, current: false, state: "decided", decision: "reject" }, meta: {},
    });
    const client = new QueryClient();
    render(<QueryClientProvider client={client}>
      <OwnerDeliveryReceipt presentationId="old-presentation" />
    </QueryClientProvider>);
    openReceipt();
    await waitFor(() => expect(screen.getByText("reject")).not.toBeNull());
    expect(screen.getByText("This presentation is stale. Receipt only.")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Acknowledge receipt" })).toBeNull();
    expect(getPersistedOwnerDeliveryWhy).toHaveBeenCalledExactlyOnceWith("old-presentation");
    expect(acknowledgeOwnerDelivery).not.toHaveBeenCalled();
  });
});
