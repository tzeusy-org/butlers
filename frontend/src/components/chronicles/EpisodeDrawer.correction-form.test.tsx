// @vitest-environment jsdom
/**
 * EpisodeDrawer — correction form interaction (JARVIS audit move 6,
 * bu-86c4c.15).
 *
 * The static-markup suite (EpisodeDrawer.test.tsx) covers rendering; this
 * file exercises the actual submit -> real mutation wiring and the
 * clear-on-success behavior.
 */

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/hooks/use-chronicles", () => ({
  useChroniclerEpisode: vi.fn(),
  useChroniclerEpisodeEvents: vi.fn(() => ({ data: [], isLoading: false, error: null })),
  useChroniclerEpisodeCorrections: vi.fn(() => ({ data: [], isLoading: false, error: null })),
  useChroniclerEvidenceChain: vi.fn(() => ({ data: undefined, isLoading: false, error: null })),
  useChroniclerExplain: vi.fn(() => ({ mutate: vi.fn(), isPending: false, isSuccess: false, error: null })),
  useSubmitEpisodeCorrection: vi.fn(),
}));

import { EpisodeDrawerContent } from "./EpisodeDrawer";
import {
  useChroniclerEpisode,
  useSubmitEpisodeCorrection,
} from "@/hooks/use-chronicles";
import type { ChroniclerEpisode } from "@/api/types";

function makeEpisode(overrides: Partial<ChroniclerEpisode> = {}): ChroniclerEpisode {
  return {
    id: "ep-test-id",
    source_name: "work",
    source_ref: "ref-1",
    episode_type: "session",
    start_at: "2026-04-25T09:00:00Z",
    end_at: "2026-04-25T10:00:00Z",
    precision: "minute",
    title: null,
    payload: {},
    privacy: "normal",
    retention_days: null,
    tombstone_at: null,
    canonical_start_at: "2026-04-25T09:00:00Z",
    canonical_end_at: "2026-04-25T10:00:00Z",
    canonical_title: "Deep work block",
    canonical_privacy: "normal",
    corrected_at: null,
    correction_note: null,
    created_at: "2026-04-25T00:00:00Z",
    updated_at: "2026-04-25T00:00:00Z",
    category: "work",
    ...overrides,
  };
}

const mockMutate = vi.fn();

beforeEach(() => {
  mockMutate.mockClear();
  vi.mocked(useChroniclerEpisode).mockReturnValue({
    data: makeEpisode(),
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useChroniclerEpisode>);
  vi.mocked(useSubmitEpisodeCorrection).mockReturnValue({
    mutate: mockMutate,
    isPending: false,
    isSuccess: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useSubmitEpisodeCorrection>);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("EpisodeDrawer correction form — submit wiring", () => {
  it("is disabled until the note field has content", () => {
    render(<EpisodeDrawerContent episodeId="ep-test-id" />);

    const submitButton = screen.getByRole("button", {
      name: "Submit correction",
    }) as HTMLButtonElement;
    expect(submitButton.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Note"), {
      target: { value: "This was actually a client call, not deep work." },
    });

    expect(submitButton.disabled).toBe(false);
  });

  it("calls the real mutation with the episode id and note on submit", () => {
    render(<EpisodeDrawerContent episodeId="ep-test-id" />);

    fireEvent.change(screen.getByLabelText("Note"), {
      target: { value: "This was actually a client call, not deep work." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit correction" }));

    expect(mockMutate).toHaveBeenCalledTimes(1);
    expect(mockMutate).toHaveBeenCalledWith(
      {
        episodeId: "ep-test-id",
        body: {
          corrected_title: undefined,
          corrected_privacy: undefined,
          note: "This was actually a client call, not deep work.",
        },
      },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("includes a corrected title when the Title field is filled in", () => {
    render(<EpisodeDrawerContent episodeId="ep-test-id" />);

    fireEvent.change(screen.getByLabelText("Corrected title"), {
      target: { value: "Client call: Acme renewal" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit correction" }));

    expect(mockMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        body: expect.objectContaining({ corrected_title: "Client call: Acme renewal" }),
      }),
      expect.anything(),
    );
  });

  it("clears the form fields on successful submit", () => {
    render(<EpisodeDrawerContent episodeId="ep-test-id" />);

    const noteField = screen.getByLabelText("Note") as HTMLTextAreaElement;
    fireEvent.change(noteField, { target: { value: "Fixed the category." } });
    fireEvent.click(screen.getByRole("button", { name: "Submit correction" }));

    const onSuccess = mockMutate.mock.calls[0][1].onSuccess;
    act(() => {
      onSuccess();
    });

    expect(noteField.value).toBe("");
  });
});
