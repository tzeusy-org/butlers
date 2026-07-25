import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { StatusBadge } from "@/components/sessions/StatusBadge";

describe("StatusBadge", () => {
  it("renders the mandatory Success label (colour alone is below contrast floor)", () => {
    const html = renderToStaticMarkup(<StatusBadge success={true} />);
    expect(html).toContain("Success");
  });

  it("renders Failed via the destructive badge for false", () => {
    const html = renderToStaticMarkup(<StatusBadge success={false} />);
    expect(html).toContain("Failed");
  });

  it("renders Running for a null (in-flight) session", () => {
    const html = renderToStaticMarkup(<StatusBadge success={null} />);
    expect(html).toContain("Running");
  });

  it("reads colour from tokens, never raw hex", () => {
    const html = renderToStaticMarkup(<StatusBadge success={true} />);
    expect(html).toContain("var(--green)");
    expect(html).not.toMatch(/#[0-9a-fA-F]{6}/);
  });

  it("renders Cancelled (not Failed) for an owner-cancelled session (bu-ep4ks.2)", () => {
    const html = renderToStaticMarkup(
      <StatusBadge success={false} error="Cancelled by owner" />,
    );
    expect(html).toContain("Cancelled");
    expect(html).not.toContain("Failed");
  });

  it("still renders Failed for a false with an unrelated error", () => {
    const html = renderToStaticMarkup(
      <StatusBadge success={false} error="RuntimeError: exit code 1" />,
    );
    expect(html).toContain("Failed");
    expect(html).not.toContain("Cancelled");
  });
});
