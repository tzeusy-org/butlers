// @vitest-environment jsdom
/**
 * Tests for <SpendVerdictOpener> (bu-qvnce.9, JARVIS pursuit move 9 slice 2).
 *
 * Verifies the /spend opener composes pace + projection_confidence
 * (fetched but discarded before this change -- SpendPage.tsx:80) + top mover
 * into one calm line, and honors the isError-suppression contract for its
 * own inputs (it deliberately does not restate the over-ceiling condition,
 * which SpendPage already surfaces via a dedicated alert banner).
 */

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { SpendVerdictOpener } from "@/components/costs/SpendVerdictOpener";
import type { ForecastData } from "@/lib/spend-forecast";

function render(ui: React.ReactElement): string {
  return renderToStaticMarkup(<MemoryRouter>{ui}</MemoryRouter>);
}

const FORECAST: ForecastData = {
  days: [],
  projected_eom_usd: 100,
  days_in_month: 30,
  days_elapsed: 10,
  mtd_usd: 50,
  ceiling_usd: null,
  projection_confidence: "normal",
};

describe("SpendVerdictOpener -- all clear", () => {
  it("composes pace, confidence, and the top mover into one calm line", () => {
    const html = render(
      <SpendVerdictOpener
        forecast={FORECAST}
        forecastLoading={false}
        forecastError={false}
        currentByButler={{ inbox: 20, calendar: 5 }}
        priorByButler={{ inbox: 10, calendar: 5 }}
        unavailableButlers={new Set()}
        moversLoading={false}
        moversError={false}
      />,
    );
    expect(html).toContain('data-testid="spend-verdict-all-clear"');
    expect(html).toContain("$5.00/day pace"); // 50 / 10 days
    expect(html).toContain("normal-confidence projection");
    expect(html).toContain("top mover inbox +$10.00");
  });

  it("surfaces a low-confidence projection instead of discarding it", () => {
    const html = render(
      <SpendVerdictOpener
        forecast={{ ...FORECAST, projection_confidence: "low" }}
        forecastLoading={false}
        forecastError={false}
        currentByButler={{}}
        priorByButler={{}}
        unavailableButlers={new Set()}
        moversLoading={false}
        moversError={false}
      />,
    );
    expect(html).toContain("low-confidence projection");
  });

  it("omits the top-mover clause when nothing changed vs the prior window", () => {
    const html = render(
      <SpendVerdictOpener
        forecast={FORECAST}
        forecastLoading={false}
        forecastError={false}
        currentByButler={{ inbox: 10 }}
        priorByButler={{ inbox: 10 }}
        unavailableButlers={new Set()}
        moversLoading={false}
        moversError={false}
      />,
    );
    expect(html).not.toContain("top mover");
  });
});

describe("SpendVerdictOpener -- clauses", () => {
  it("names butlers excluded from the spend comparison instead of fabricating a mover", () => {
    const html = render(
      <SpendVerdictOpener
        forecast={FORECAST}
        forecastLoading={false}
        forecastError={false}
        currentByButler={{ inbox: 10 }}
        priorByButler={{}}
        unavailableButlers={new Set(["inbox"])}
        moversLoading={false}
        moversError={false}
      />,
    );
    expect(html).toContain('data-testid="spend-verdict-clauses"');
    expect(html).toContain("excluded from spend comparison");
    expect(html).toContain("inbox");
    expect(html).not.toContain("$/day pace");
  });
});

describe("SpendVerdictOpener -- isError-suppression contract", () => {
  it("renders the skeleton while the forecast or comparison sources are loading", () => {
    const html = render(
      <SpendVerdictOpener
        forecast={undefined}
        forecastLoading
        forecastError={false}
        currentByButler={{}}
        priorByButler={{}}
        unavailableButlers={new Set()}
        moversLoading={false}
        moversError={false}
      />,
    );
    expect(html).toContain('data-testid="spend-verdict-skeleton"');
  });

  it("names an errored comparison source and never renders the pace line alongside it", () => {
    const html = render(
      <SpendVerdictOpener
        forecast={FORECAST}
        forecastLoading={false}
        forecastError={false}
        currentByButler={{}}
        priorByButler={{}}
        unavailableButlers={new Set()}
        moversLoading={false}
        moversError
      />,
    );
    expect(html).toContain("spend comparison unavailable");
    expect(html).not.toContain("pace");
  });
});
