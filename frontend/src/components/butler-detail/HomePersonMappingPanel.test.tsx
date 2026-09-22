// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({ submitHomePersonMappings: vi.fn() }));

import { submitHomePersonMappings } from "@/api/client";

import { HomePersonMappingPanel } from "./HomePersonMappingPanel";

afterEach(() => vi.clearAllMocks());

describe("HomePersonMappingPanel", () => {
  it("keeps private values ephemeral and renders only aggregate settlement", async () => {
    vi.mocked(submitHomePersonMappings).mockResolvedValue({
      data: {
        receipt: "00000000-0000-4000-8000-000000000001",
        complete: true,
        received_count: 1,
        created_count: 1,
        unchanged_count: 0,
        conflict_count: 0,
        invalid_reference_count: 0,
      },
      meta: {},
    });
    render(<HomePersonMappingPanel />);
    const privateHa = "person.private_ui_sentinel";
    const privateEntity = "00000000-0000-4000-8000-000000000002";

    fireEvent.change(screen.getByLabelText("Home Assistant person ID 1"), {
      target: { value: privateHa },
    });
    fireEvent.change(screen.getByLabelText("Person entity UUID 1"), {
      target: { value: privateEntity },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit mappings" }));

    await screen.findByText("Complete. Created 1; unchanged 0.");
    expect((screen.getByLabelText("Home Assistant person ID 1") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Person entity UUID 1") as HTMLInputElement).value).toBe("");
    await waitFor(() => expect(document.body.textContent).not.toContain(privateHa));
    expect(document.body.textContent).not.toContain(privateEntity);
    expect(submitHomePersonMappings).toHaveBeenCalledWith(
      [{ ha_person_id: privateHa, entity_id: privateEntity }],
      expect.stringMatching(/^[A-Za-z0-9_-]{43}$/),
    );
  });
});
