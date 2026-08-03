// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import ButlerSkillsTab from "./ButlerSkillsTab";

vi.mock("@/hooks/use-butlers", () => ({
  useButlerSkills: vi.fn(),
}));

import { useButlerSkills } from "@/hooks/use-butlers";

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
});
