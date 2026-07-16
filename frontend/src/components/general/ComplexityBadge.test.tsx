import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ComplexityBadge } from "./ComplexityBadge";

describe("ComplexityBadge", () => {
  it.each([
    ["reasoning", "categorical-1"],
    ["workhorse", "categorical-2"],
    ["cheap", "categorical-3"],
    ["specialty", "categorical-4"],
    ["local", "categorical-5"],
    ["legacy", "categorical-6"],
  ] as const)("renders %s as an outlined categorical label", (tier, color) => {
    const html = renderToStaticMarkup(<ComplexityBadge tier={tier} />);

    expect(html).toContain(`>${tier[0].toUpperCase() + tier.slice(1)}</span>`);
    expect(html).toContain('data-variant="outline"');
    expect(html).toContain(`border-${color}`);
    expect(html).toContain(`text-${color}`);
  });

  it("keeps unknown tiers neutral", () => {
    const html = renderToStaticMarkup(<ComplexityBadge tier="custom" />);

    expect(html).toContain(">custom</span>");
    expect(html).toContain("text-muted-foreground");
  });
});
