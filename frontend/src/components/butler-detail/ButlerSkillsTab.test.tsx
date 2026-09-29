// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";

import ButlerSkillsTab from "./ButlerSkillsTab";
import { ButlerCommandBarPrefillProvider, useCommandBarPrefill } from "./command-bar-prefill";

vi.mock("@/hooks/use-butlers", () => ({
  useButlerSkills: vi.fn(),
}));

import { useButlerSkills } from "@/hooks/use-butlers";

const ENTRY = "/butlers/general?tab=system&section=skills";

function mockSkills() {
  vi.mocked(useButlerSkills).mockReturnValue({
    data: { data: [{ name: "foo", content: "Does foo things." }], meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useButlerSkills>);
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

function PendingProbe() {
  const { pending } = useCommandBarPrefill();
  return <output data-testid="pending">{pending ?? "none"}</output>;
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("ButlerSkillsTab", () => {
  it("shows the failure state instead of the empty state when cached skills are empty", () => {
    vi.mocked(useButlerSkills).mockReturnValue({
      data: { data: [], meta: {} },
      isLoading: false,
      isError: true,
      error: new Error("Skills service unavailable"),
    } as unknown as ReturnType<typeof useButlerSkills>);

    render(
      <MemoryRouter>
        <ButlerSkillsTab butlerName="general" />
      </MemoryRouter>,
    );

    expect(screen.getByText("Failed to load skills: Skills service unavailable")).toBeDefined();
    expect(screen.queryByText("No skills registered")).toBeNull();
  });

  it("Use skill asks the command bar for a prefill and keeps the current tab", () => {
    mockSkills();
    render(
      <MemoryRouter initialEntries={[ENTRY]}>
        <ButlerCommandBarPrefillProvider>
          <ButlerSkillsTab butlerName="general" />
          <PendingProbe />
        </ButlerCommandBarPrefillProvider>
        <LocationProbe />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Use skill" }));

    expect(screen.getByTestId("pending").textContent).toBe("Use the foo skill to ");
    expect(screen.getByTestId("location").textContent).toBe(ENTRY);
  });

  it("Use skill is a harmless no-op without a command bar provider", () => {
    mockSkills();
    render(
      <MemoryRouter initialEntries={[ENTRY]}>
        <ButlerSkillsTab butlerName="general" />
        <LocationProbe />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Use skill" }));

    expect(screen.getByTestId("location").textContent).toBe(ENTRY);
  });
});
