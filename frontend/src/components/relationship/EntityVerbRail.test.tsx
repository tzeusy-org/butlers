// @vitest-environment jsdom
/**
 * EntityVerbRail — the three entity operator verbs plus notes (bu-6t8ix.4).
 *
 * PR #2894 shipped none of these because the backend had no write path, so
 * the thing worth testing is that each chip now reaches a real mutation with
 * the right payload, and that the two states a fact write can land in
 * (already-exists, refused) are visible to the operator rather than silent.
 *
 * The draft-reach-out verb gets its own assertion that nothing in its
 * payload or copy implies a send: "draft" must mean drafted.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ApiError } from "@/api/client";

vi.mock("@/hooks/use-entities", () => ({
  useCreateEntityNote: vi.fn(),
  useCreateEntityInteraction: vi.fn(),
  useCreateEntityGift: vi.fn(),
  useCreateEntityReachOutDraft: vi.fn(),
}));

import { EntityVerbRail, verbErrorMessage } from "./EntityVerbRail";
import {
  useCreateEntityGift,
  useCreateEntityInteraction,
  useCreateEntityNote,
  useCreateEntityReachOutDraft,
} from "@/hooks/use-entities";

const ENTITY_ID = "11111111-2222-3333-4444-555555555555";

const noteMutate = vi.fn();
const interactionMutate = vi.fn();
const giftMutate = vi.fn();
const draftMutate = vi.fn();

type MutationState = {
  isPending?: boolean;
  isSuccess?: boolean;
  error?: unknown;
};

function mutationResult(mutate: unknown, state: MutationState = {}) {
  return {
    mutate,
    isPending: state.isPending ?? false,
    isSuccess: state.isSuccess ?? false,
    isError: !!state.error,
    error: state.error ?? null,
  } as unknown as ReturnType<typeof useCreateEntityNote>;
}

function setMutations(states: Partial<Record<string, MutationState>> = {}) {
  vi.mocked(useCreateEntityNote).mockReturnValue(
    mutationResult(noteMutate, states.note),
  );
  vi.mocked(useCreateEntityInteraction).mockReturnValue(
    mutationResult(interactionMutate, states.interaction) as unknown as ReturnType<
      typeof useCreateEntityInteraction
    >,
  );
  vi.mocked(useCreateEntityGift).mockReturnValue(
    mutationResult(giftMutate, states.gift) as unknown as ReturnType<typeof useCreateEntityGift>,
  );
  vi.mocked(useCreateEntityReachOutDraft).mockReturnValue(
    mutationResult(draftMutate, states.draft) as unknown as ReturnType<
      typeof useCreateEntityReachOutDraft
    >,
  );
}

beforeEach(() => {
  noteMutate.mockClear();
  interactionMutate.mockClear();
  giftMutate.mockClear();
  draftMutate.mockClear();
  setMutations();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("EntityVerbRail — chips", () => {
  it("renders all four verbs collapsed", () => {
    render(<EntityVerbRail entityId={ENTITY_ID} />);

    expect(screen.getByTestId("verb-chip-log-interaction")).toBeTruthy();
    expect(screen.getByTestId("verb-chip-gift-idea")).toBeTruthy();
    expect(screen.getByTestId("verb-chip-draft-reach-out")).toBeTruthy();
    expect(screen.getByTestId("verb-chip-note")).toBeTruthy();
    // Collapsed: no form is mounted until a chip is clicked.
    expect(screen.queryByLabelText("Log an interaction")).toBeNull();
    expect(screen.queryByLabelText("Capture a gift idea")).toBeNull();
    expect(screen.queryByLabelText("Draft a reach-out")).toBeNull();
  });

  it("opens one form at a time and toggles it closed again", () => {
    render(<EntityVerbRail entityId={ENTITY_ID} />);

    fireEvent.click(screen.getByTestId("verb-chip-gift-idea"));
    expect(screen.getByLabelText("Capture a gift idea")).toBeTruthy();
    expect(screen.queryByLabelText("Log an interaction")).toBeNull();

    fireEvent.click(screen.getByTestId("verb-chip-log-interaction"));
    expect(screen.getByLabelText("Log an interaction")).toBeTruthy();
    expect(screen.queryByLabelText("Capture a gift idea")).toBeNull();

    fireEvent.click(screen.getByTestId("verb-chip-log-interaction"));
    expect(screen.queryByLabelText("Log an interaction")).toBeNull();
  });

  it("omits the heading in compact mode (Plex dossier)", () => {
    const { rerender } = render(<EntityVerbRail entityId={ENTITY_ID} />);
    expect(screen.getByText("Record something")).toBeTruthy();

    rerender(<EntityVerbRail entityId={ENTITY_ID} compact />);
    expect(screen.queryByText("Record something")).toBeNull();
  });
});

describe("log-interaction verb", () => {
  it("submits the selected type and summary to the real mutation", () => {
    render(<EntityVerbRail entityId={ENTITY_ID} />);
    fireEvent.click(screen.getByTestId("verb-chip-log-interaction"));

    fireEvent.change(screen.getByLabelText("What happened"), {
      target: { value: "Caught up about the move" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save interaction" }));

    expect(interactionMutate).toHaveBeenCalledTimes(1);
    expect(interactionMutate).toHaveBeenCalledWith(
      {
        entityId: ENTITY_ID,
        request: { type: "call", summary: "Caught up about the move" },
      },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("submits with a null summary rather than an empty string", () => {
    render(<EntityVerbRail entityId={ENTITY_ID} />);
    fireEvent.click(screen.getByTestId("verb-chip-log-interaction"));
    fireEvent.click(screen.getByRole("button", { name: "Save interaction" }));

    expect(interactionMutate).toHaveBeenCalledWith(
      { entityId: ENTITY_ID, request: { type: "call", summary: null } },
      expect.anything(),
    );
  });

  it("names the already-logged case instead of showing a generic failure", () => {
    setMutations({
      interaction: {
        error: new ApiError("UNKNOWN_ERROR", "{}", 409, {
          code: "duplicate_interaction",
          existing_id: "abc",
        }),
      },
    });
    render(<EntityVerbRail entityId={ENTITY_ID} />);
    fireEvent.click(screen.getByTestId("verb-chip-log-interaction"));

    expect(screen.getByTestId("log-interaction-error").textContent).toContain(
      "already logged",
    );
  });
});

describe("gift-idea verb", () => {
  it("stays disabled until a description is typed", () => {
    render(<EntityVerbRail entityId={ENTITY_ID} />);
    fireEvent.click(screen.getByTestId("verb-chip-gift-idea"));

    const submit = screen.getByRole("button", { name: "Save gift idea" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Gift idea"), {
      target: { value: "Hand-thrown mug" },
    });
    expect(submit.disabled).toBe(false);
  });

  it("submits description and occasion", () => {
    render(<EntityVerbRail entityId={ENTITY_ID} />);
    fireEvent.click(screen.getByTestId("verb-chip-gift-idea"));

    fireEvent.change(screen.getByLabelText("Gift idea"), {
      target: { value: "  Hand-thrown mug  " },
    });
    fireEvent.change(screen.getByLabelText("Occasion"), {
      target: { value: "housewarming" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save gift idea" }));

    expect(giftMutate).toHaveBeenCalledWith(
      {
        entityId: ENTITY_ID,
        request: { description: "Hand-thrown mug", occasion: "housewarming" },
      },
      expect.anything(),
    );
  });

  it("reports an already-captured idea as a duplicate", () => {
    setMutations({
      gift: { error: new ApiError("UNKNOWN_ERROR", "{}", 409, { code: "duplicate_gift" }) },
    });
    render(<EntityVerbRail entityId={ENTITY_ID} />);
    fireEvent.click(screen.getByTestId("verb-chip-gift-idea"));

    expect(screen.getByTestId("gift-idea-error").textContent).toContain("already on the list");
  });
});

describe("draft-reach-out verb", () => {
  it("submits a message and a channel and says nothing was sent", () => {
    render(<EntityVerbRail entityId={ENTITY_ID} />);
    fireEvent.click(screen.getByTestId("verb-chip-draft-reach-out"));

    fireEvent.change(screen.getByLabelText("Draft message"), {
      target: { value: "Been a while, coffee next week?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));

    expect(draftMutate).toHaveBeenCalledWith(
      {
        entityId: ENTITY_ID,
        request: { message: "Been a while, coffee next week?", channel: "telegram" },
      },
      expect.anything(),
    );
  });

  it("tells the operator up front that nothing is sent", () => {
    render(<EntityVerbRail entityId={ENTITY_ID} />);
    fireEvent.click(screen.getByTestId("verb-chip-draft-reach-out"));

    expect(screen.getByTestId("draft-reach-out-inert-note").textContent).toContain(
      "Nothing is sent",
    );
  });

  it("offers no send affordance anywhere in the form", () => {
    render(<EntityVerbRail entityId={ENTITY_ID} />);
    fireEvent.click(screen.getByTestId("verb-chip-draft-reach-out"));

    const buttonLabels = screen
      .getAllByRole("button")
      .map((b) => (b.textContent ?? "").toLowerCase());
    expect(buttonLabels.some((label) => label.includes("send"))).toBe(false);
  });

  it("confirms on success that the draft was saved and not sent", () => {
    setMutations({ draft: { isSuccess: true } });
    render(<EntityVerbRail entityId={ENTITY_ID} />);
    fireEvent.click(screen.getByTestId("verb-chip-draft-reach-out"));

    expect(screen.getByTestId("draft-reach-out-success").textContent).toContain("Nothing was sent");
  });
});

describe("note verb", () => {
  it("submits trimmed content", () => {
    render(<EntityVerbRail entityId={ENTITY_ID} />);
    fireEvent.click(screen.getByTestId("verb-chip-note"));

    fireEvent.change(screen.getByLabelText("Note"), {
      target: { value: "  Moving in October  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save note" }));

    expect(noteMutate).toHaveBeenCalledWith(
      { entityId: ENTITY_ID, request: { content: "Moving in October" } },
      expect.anything(),
    );
  });

  it("shows a pending state while the write is in flight", () => {
    setMutations({ note: { isPending: true } });
    render(<EntityVerbRail entityId={ENTITY_ID} />);
    fireEvent.click(screen.getByTestId("verb-chip-note"));

    expect(screen.getByTestId("entity-note-pending")).toBeTruthy();
    // Honest-pending: no success line until the server confirms.
    expect(screen.queryByTestId("entity-note-success")).toBeNull();
  });
});

describe("verbErrorMessage", () => {
  it("names the duplicate case with the caller's wording", () => {
    const err = new ApiError("UNKNOWN_ERROR", "{}", 409, { code: "duplicate_note" });
    expect(verbErrorMessage(err, "Already recorded.")).toBe("Already recorded.");
  });

  it("explains the owner gate rather than echoing a JSON blob", () => {
    const err = new ApiError("UNKNOWN_ERROR", '{"code":"owner_required"}', 403, {
      code: "owner_required",
    });
    expect(verbErrorMessage(err, "dup")).toContain("owner");
  });

  it("surfaces the tool's own message on a 422", () => {
    const err = new ApiError("UNKNOWN_ERROR", "{}", 422, {
      code: "invalid_interaction",
      message: "Invalid direction 'sideways'.",
    });
    expect(verbErrorMessage(err, "dup")).toBe("Invalid direction 'sideways'.");
  });

  it("falls back to a plain sentence for a non-API failure", () => {
    expect(verbErrorMessage(new Error(""), "dup")).toContain("Nothing was recorded");
  });
});
