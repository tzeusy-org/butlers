// @vitest-environment jsdom
/**
 * ContactPeoplePicker — RTL tests (bu-hzi4v).
 *
 * Covers:
 *  - Typing a query calls GET /api/contacts/search (via searchContacts) with
 *    the typed query, and renders matches as selectable results.
 *  - Selecting a match calls onChange with the person's entity_id (the value the
 *    calendar dialog threads into `entity_ids[]` on the create mutation).
 *  - Already-selected people render as removable chips and are excluded from
 *    the result list; removing a chip calls onChange without them.
 *  - A failed search renders an honest degraded note, never a silently-empty
 *    list.
 *
 * `searchContacts` is mocked so tests are deterministic and don't hit a backend.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ContactPeoplePicker, type SelectedPerson } from "./ContactPeoplePicker";
import type { ContactSearchResponse } from "@/api/types.ts";

const searchContactsMock = vi.fn();
vi.mock("@/api/index.ts", () => ({
  searchContacts: (...args: unknown[]) => searchContactsMock(...args),
}));

function ok(results: ContactSearchResponse["results"]): ContactSearchResponse {
  return { results };
}

function renderPicker(
  value: SelectedPerson[],
  onChange: (people: SelectedPerson[]) => void,
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ContactPeoplePicker value={value} onChange={onChange} debounceMs={0} />
    </QueryClientProvider>,
  );
}

afterEach(cleanup);
beforeEach(() => {
  searchContactsMock.mockReset();
});

const ADA = {
  entity_id: "11111111-1111-1111-1111-111111111111",
  canonical_name: "Ada Lovelace",
  matched_identifier: { type: "email", value: "ada@example.com" },
};

describe("ContactPeoplePicker", () => {
  it("queries the search endpoint with the typed query and renders matches", async () => {
    searchContactsMock.mockResolvedValue(ok([ADA]));
    renderPicker([], () => {});

    fireEvent.change(screen.getByTestId("people-search-input"), {
      target: { value: "ada" },
    });

    await waitFor(() => {
      expect(searchContactsMock).toHaveBeenCalled();
    });
    expect(searchContactsMock.mock.calls[0][0]).toBe("ada");

    const result = await screen.findByTestId("people-search-result");
    expect(within(result).getByText("Ada Lovelace")).toBeTruthy();
    expect(within(result).getByText("ada@example.com")).toBeTruthy();
  });

  it("threads the entity_id into onChange when a match is selected", async () => {
    searchContactsMock.mockResolvedValue(ok([ADA]));
    const onChange = vi.fn();
    renderPicker([], onChange);

    fireEvent.change(screen.getByTestId("people-search-input"), {
      target: { value: "ada" },
    });

    const result = await screen.findByTestId("people-search-result");
    fireEvent.click(result);

    expect(onChange).toHaveBeenCalledWith([
      { entity_id: ADA.entity_id, canonical_name: "Ada Lovelace" },
    ]);
  });

  it("keeps the sole selected person removable and excludes them from results", async () => {
    searchContactsMock.mockResolvedValue(ok([ADA]));
    const onChange = vi.fn();
    renderPicker(
      [{ entity_id: ADA.entity_id, canonical_name: "Ada Lovelace" }],
      onChange,
    );

    const chip = screen.getByTestId("people-selected-chip");
    expect(within(chip).getByText("Ada Lovelace")).toBeTruthy();

    // Searching for the already-linked person yields no selectable result.
    fireEvent.change(screen.getByTestId("people-search-input"), {
      target: { value: "ada" },
    });
    await waitFor(() => expect(searchContactsMock).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByTestId("people-search-result")).toBeNull(),
    );

    const removeButton = screen.getByTestId("people-remove-chip") as HTMLButtonElement;
    expect(removeButton.disabled).toBe(false);
    expect(removeButton.tagName).toBe("BUTTON");
    expect(removeButton.getAttribute("aria-label")).toBe("Remove Ada Lovelace");
    expect(removeButton.className).toContain("h-6");
    expect(removeButton.className).toContain("w-6");
    expect(removeButton.className).toContain("focus-visible:ring-2");
    expect(screen.queryByTestId("people-preserve-last-note")).toBeNull();
    fireEvent.click(removeButton);
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("renders an honest degraded note when the search fails", async () => {
    searchContactsMock.mockRejectedValue(new Error("boom"));
    renderPicker([], () => {});

    fireEvent.change(screen.getByTestId("people-search-input"), {
      target: { value: "ada" },
    });

    const degraded = await screen.findByTestId("people-search-degraded");
    expect(degraded.textContent).toContain("unavailable");
    // Never a silently-empty selectable list.
    expect(screen.queryByTestId("people-search-result")).toBeNull();
    expect(screen.queryByTestId("people-search-empty")).toBeNull();
  });

});
