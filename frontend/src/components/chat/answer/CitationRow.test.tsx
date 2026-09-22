// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CitationRow } from "./CitationRow";

const navigate = vi.fn();

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useNavigate: () => navigate };
});

afterEach(() => {
  cleanup();
  navigate.mockReset();
});

describe("CitationRow", () => {
  it("uses router navigation for an internal citation without assigning location", () => {
    const originalHref = window.location.href;
    render(
      <CitationRow
        citations={[{ label: "Budget detail", target: "/spend", kind: "internal" }]}
      />,
    );

    fireEvent.click(screen.getByRole("link", { name: "Budget detail" }));
    expect(navigate).toHaveBeenCalledWith("/spend");
    expect(window.location.href).toBe(originalHref);
  });

  it("renders external https safely and legacy citations as honest text", () => {
    render(
      <CitationRow
        citations={[
          { label: "Reference", target: "https://example.com/reference", kind: "external" },
          { label: "finance.get_budget", target: null, kind: "unlinked" },
        ]}
      />,
    );

    const external = screen.getByRole("link", { name: /Reference/ });
    expect(external.getAttribute("href")).toBe("https://example.com/reference");
    expect(external.getAttribute("rel")).toBe("noopener noreferrer");
    expect(screen.getByText("finance.get_budget").closest("a")).toBeNull();
  });
});
