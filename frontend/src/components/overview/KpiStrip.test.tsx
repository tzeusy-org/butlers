// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";

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
