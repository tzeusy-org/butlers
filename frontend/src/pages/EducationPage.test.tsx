// @vitest-environment jsdom
/**
 * EducationPage — three-way loading/error/empty state contract (bu-mkd5r).
 *
 * Reviewer-59h0k flagged EducationPage as hardcoding "No curriculums yet." with
 * no isError check, so a down education backend rendered a calm all-clear. These
 * tests pin the contract: a failed fetch renders an honest error-with-retry, and
 * the empty state renders ONLY on a genuinely empty successful fetch.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router";

vi.mock("@/hooks/use-education", () => ({
  useMindMaps: vi.fn(),
}));

// The command palette registry needs no provider in these branches; stub it so
// the page under test does not depend on the palette context being mounted.
vi.mock("@/lib/command-registry", () => ({
  useRegisterCommands: vi.fn(),
}));

// The request dialog pulls in form/query machinery irrelevant to the state
// contract under test — stub it to a marker element.
vi.mock("@/components/education/RequestCurriculumDialog", () => ({
  default: () => <div data-testid="request-curriculum-dialog" />,
}));

import { useMindMaps } from "@/hooks/use-education";
import EducationPage from "@/pages/EducationPage";

const mockUseMindMaps = vi.mocked(useMindMaps);

function renderPage() {
  return render(
    <MemoryRouter>
      <EducationPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(cleanup);

describe("EducationPage state contract", () => {
  it("renders an error state (not 'No curriculums yet.') when the fetch fails", () => {
    mockUseMindMaps.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMindMaps>);

    const { getByTestId, getByRole, queryByText } = renderPage();
    const alert = getByTestId("education-error");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(getByRole("button", { name: /retry/i })).toBeTruthy();
    expect(queryByText("No curriculums yet.")).toBeNull();
  });

  it("renders the empty state only on a genuinely empty successful fetch", () => {
    mockUseMindMaps.mockReturnValue({
      data: { data: [] },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMindMaps>);

    const { getByText, queryByTestId } = renderPage();
    expect(getByText("No curriculums yet.")).toBeTruthy();
    expect(queryByTestId("education-error")).toBeNull();
  });
});
