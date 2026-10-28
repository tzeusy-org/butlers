// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import { DebriefCard } from "@/components/calendar/DebriefCard";
import { getPendingMeetingDebriefs } from "@/api/client.ts";
import { sendDebriefReply } from "@/lib/debrief-reply.ts";

vi.mock("@/api/client.ts", () => ({ getPendingMeetingDebriefs: vi.fn() }));
vi.mock("@/lib/debrief-reply.ts", () => ({ sendDebriefReply: vi.fn() }));

const DEBRIEF = {
  debrief_id: "d1",
  number: 3,
  event_title: "Roadmap sync",
  occurrence_start: "2026-10-18T09:00:00Z",
  prompted_at: "2026-10-18T11:00:00Z",
  attendees: [
    { entity_id: "e1", name: "Sam Example", email: "sam@example.test" },
    { entity_id: null, name: null, email: "guest@example.test" },
  ],
};

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DebriefCard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

beforeEach(() => {
  vi.mocked(getPendingMeetingDebriefs).mockReset();
  vi.mocked(sendDebriefReply).mockReset();
});
afterEach(cleanup);

describe("DebriefCard", () => {
  it("lists pending debriefs with number, title and attendees", async () => {
    vi.mocked(getPendingMeetingDebriefs).mockResolvedValue({
      debriefs: [DEBRIEF],
      debriefs_available: true,
    });
    renderCard();

    const card = await screen.findByTestId("debrief-card");
    expect(card.textContent).toContain("Roadmap sync");
    expect(card.textContent).toContain("3");
    expect(card.textContent).toContain("Sam Example, guest@example.test");
  });

  it("is hidden when nothing is pending", async () => {
    vi.mocked(getPendingMeetingDebriefs).mockResolvedValue({
      debriefs: [],
      debriefs_available: true,
    });
    const { container } = renderCard();

    await waitFor(() => expect(getPendingMeetingDebriefs).toHaveBeenCalled());
    await Promise.resolve();
    expect(container.querySelector('[data-testid="debrief-card"]')).toBeNull();
    expect(container.querySelector('[data-testid="debrief-card-degraded"]')).toBeNull();
  });

  it("shows a degraded note, not an empty card, when the read is unavailable", async () => {
    vi.mocked(getPendingMeetingDebriefs).mockResolvedValue({
      debriefs: [],
      debriefs_available: false,
    });
    renderCard();

    expect(await screen.findByTestId("debrief-card-degraded")).toBeTruthy();
    expect(screen.queryByTestId("debrief-card")).toBeNull();
  });

  it("shows a degraded note when the request fails", async () => {
    vi.mocked(getPendingMeetingDebriefs).mockRejectedValue(new Error("boom"));
    renderCard();

    expect(await screen.findByTestId("debrief-card-degraded")).toBeTruthy();
  });

  it("sends the reply with its number, refetches, and links the conversation", async () => {
    vi.mocked(getPendingMeetingDebriefs).mockResolvedValue({
      debriefs: [DEBRIEF],
      debriefs_available: true,
    });
    vi.mocked(sendDebriefReply).mockResolvedValue("conv-9");
    renderCard();

    const input = (await screen.findByTestId("debrief-reply-input")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "send Sam the deck by Friday" } });
    fireEvent.click(screen.getByTestId("debrief-reply-send"));

    await waitFor(() =>
      expect(sendDebriefReply).toHaveBeenCalledWith(
        DEBRIEF,
        "send Sam the deck by Friday",
        expect.any(String),
      ),
    );
    const link = await screen.findByTestId("debrief-reply-conversation");
    expect(link.getAttribute("href")).toBe("/chat/conv-9");
    await waitFor(() => expect(getPendingMeetingDebriefs).toHaveBeenCalledTimes(2));
    expect(input.value).toBe("");
  });

  it("keeps the text and shows an inline error when the send fails", async () => {
    vi.mocked(getPendingMeetingDebriefs).mockResolvedValue({
      debriefs: [DEBRIEF],
      debriefs_available: true,
    });
    vi.mocked(sendDebriefReply).mockRejectedValue(new Error("HTTP 503"));
    renderCard();

    const input = (await screen.findByTestId("debrief-reply-input")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "nothing agreed" } });
    fireEvent.click(screen.getByTestId("debrief-reply-send"));

    expect((await screen.findByTestId("debrief-reply-error")).textContent).toBe("HTTP 503");
    expect(input.value).toBe("nothing agreed");

    // A retry of the same text reuses the message id; a changed text gets a new one.
    vi.mocked(sendDebriefReply).mockRejectedValue(new Error("HTTP 503"));
    fireEvent.click(screen.getByTestId("debrief-reply-send"));
    await waitFor(() => expect(sendDebriefReply).toHaveBeenCalledTimes(2));
    const [first, second] = vi.mocked(sendDebriefReply).mock.calls.map((c) => c[2]);
    expect(second).toBe(first);
    fireEvent.change(input, { target: { value: "none" } });
    fireEvent.click(screen.getByTestId("debrief-reply-send"));
    await waitFor(() => expect(sendDebriefReply).toHaveBeenCalledTimes(3));
    expect(vi.mocked(sendDebriefReply).mock.calls[2][2]).not.toBe(first);
    expect(screen.getByTestId("debrief-card")).toBeTruthy();
    expect(getPendingMeetingDebriefs).toHaveBeenCalledTimes(1);
  });

  it("sends each duplicate-numbered debrief with its own record", async () => {
    const other = { ...DEBRIEF, debrief_id: "d2", event_title: "Other sync" };
    vi.mocked(getPendingMeetingDebriefs).mockResolvedValue({
      debriefs: [{
...DEBRIEF, number: 1 }, {
...other, number: 1 }],
      debriefs_available: true,
    });
    vi.mocked(sendDebriefReply).mockResolvedValue("conv-1");
    renderCard();

    const inputs = await screen.findAllByTestId("debrief-reply-input");
    fireEvent.change(inputs[1], { target: { value: "none" } });
    fireEvent.click(screen.getAllByTestId("debrief-reply-send")[1]);

    await waitFor(() => expect(sendDebriefReply).toHaveBeenCalledTimes(1));
    expect(vi.mocked(sendDebriefReply).mock.calls[0][0].debrief_id).toBe("d2");
  });
});
