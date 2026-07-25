// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import { KpiStrip } from "./KpiStrip";

afterEach(cleanup);

describe("KpiStrip", () => {
  it("renders four cells with value and optional delta", () => {
    const { container, getByText } = render(
      <KpiStrip
        cells={[
          { eyebrow: "Weight", value: "72", delta: "7d" },
          { eyebrow: "Blood pressure", value: "118/76" },
          { eyebrow: "Heart rate", value: "62", delta: "2h" },
          { eyebrow: "Blood sugar", value: "95" },
        ]}
      />,
    );
    const group = container.querySelector('[role="group"]');
    expect(group).toBeTruthy();
    expect(group!.children.length).toBe(4);
    expect(getByText("7d")).toBeTruthy();
    expect(getByText("2h")).toBeTruthy();
  });

  it("tints the delta amber past SLA and stays muted otherwise", () => {
    const { getByText } = render(
      <KpiStrip
        cells={[
          { eyebrow: "Weight", value: "72", delta: "9d", deltaTone: "amber" },
          { eyebrow: "Blood pressure", value: "118/76", delta: "1h", deltaTone: "muted" },
          { eyebrow: "Heart rate", value: "62", delta: "30m" },
          { eyebrow: "Blood sugar", value: "95" },
        ]}
      />,
    );
    expect((getByText("9d") as HTMLElement).style.color).toBe("var(--amber-text)");
    expect((getByText("1h") as HTMLElement).style.color).toBe("var(--muted-foreground)");
  });

  it("exposes the cell title (data source) tooltip when provided", () => {
    const { container } = render(
      <KpiStrip
        cells={[
          { eyebrow: "Weight", value: "72", delta: "7d", title: "Source: google_health" },
          { eyebrow: "Blood pressure", value: "118/76" },
          { eyebrow: "Heart rate", value: "62" },
          { eyebrow: "Blood sugar", value: "95" },
        ]}
      />,
    );
    const titled = container.querySelector('[title="Source: google_health"]');
    expect(titled).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Optional cell doors (bu-27dxl.8.3)
// ---------------------------------------------------------------------------

describe("KpiStrip: optional cell doors", () => {
  it("renders a cell with an href as a navigable link", () => {
    const { container, getByRole } = render(
      <MemoryRouter>
        <KpiStrip
          cells={[
            { eyebrow: "Total butlers", value: "4", href: "/butlers" },
            { eyebrow: "Healthy", value: "3" },
            { eyebrow: "Sessions", value: "12" },
            { eyebrow: "Pending approvals", value: "2" },
          ]}
        />
      </MemoryRouter>,
    );
    const link = getByRole("link", { name: /Total butlers/ });
    expect(link).toBeTruthy();
    expect(link.getAttribute("href")).toBe("/butlers");
    // Cells without an href stay inert -- no anchor for them.
    expect(container.querySelectorAll("a").length).toBe(1);
  });

  it("uses the explicit ariaLabel override instead of the default link name when provided", () => {
    const { getByRole } = render(
      <MemoryRouter>
        <KpiStrip
          cells={[
            {
              eyebrow: "Healthy",
              value: "3",
              href: "/butlers",
              ariaLabel: "Healthy butlers: opens the full unfiltered butler board",
            },
            { eyebrow: "Total butlers", value: "4" },
            { eyebrow: "Sessions", value: "12" },
            { eyebrow: "Pending approvals", value: "2" },
          ]}
        />
      </MemoryRouter>,
    );
    expect(
      getByRole("link", {
        name: "Healthy butlers: opens the full unfiltered butler board",
      }),
    ).toBeTruthy();
  });

  it("renders no anchor at all when no cell has an href", () => {
    const { container } = render(
      <KpiStrip
        cells={[
          { eyebrow: "Weight", value: "72" },
          { eyebrow: "Blood pressure", value: "118/76" },
          { eyebrow: "Heart rate", value: "62" },
          { eyebrow: "Blood sugar", value: "95" },
        ]}
      />,
    );
    expect(container.querySelectorAll("a").length).toBe(0);
  });
});
