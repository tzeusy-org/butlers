import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { RuntimeSummaryKpi } from "@/components/overview/RuntimeSummaryKpi";
import type { OverviewRuntimeKpis } from "./model";

const kpis: OverviewRuntimeKpis = {
  totalButlers: 4,
  healthyButlers: 3,
  sessions24h: 12,
  pendingApprovals: 2,
};

function renderComponent(overrides: Partial<Parameters<typeof RuntimeSummaryKpi>[0]> = {}): string {
  return renderToStaticMarkup(
    <RuntimeSummaryKpi
      kpis={kpis}
      isLoading={false}
      pendingApprovalsAvailable
      {...overrides}
    />,
  );
}

describe("RuntimeSummaryKpi", () => {
  it("renders the spec-approved KPI cells in first-screen order", () => {
    const html = renderComponent();

    expect(html.indexOf("Total butlers")).toBeLessThan(html.indexOf("Healthy"));
    expect(html.indexOf("Healthy")).toBeLessThan(html.indexOf("Sessions"));
    expect(html.indexOf("Sessions")).toBeLessThan(html.indexOf("Pending approvals"));
  });

  it("renders KPI values including total, healthy, sessions, and approvals", () => {
    const html = renderComponent();

    expect(html).toContain(">4<");
    expect(html).toContain(">3<");
    expect(html).toContain(">12<");
    expect(html).toContain(">2<");
  });

  it("renders zero pending approvals as a real zero", () => {
    const html = renderComponent({
      kpis: { ...kpis, pendingApprovals: 0 },
    });

    expect(html).toContain("Pending approvals");
    expect(html).toContain(">0<");
  });

  it("degrades only the approvals cell when approval metrics are unavailable", () => {
    const html = renderComponent({ pendingApprovalsAvailable: false });

    expect(html).toContain(">4<");
    expect(html).toContain(">3<");
    expect(html).toContain(">12<");
    expect(html.match(/—/g)?.length).toBe(1);
  });

  it("renders loading placeholders for all cells while the butler source is loading", () => {
    const html = renderComponent({ isLoading: true });

    expect(html.match(/—/g)?.length).toBe(4);
  });

  it("renders '—' for total/healthy/sessions on error instead of a literal 0", () => {
    // On error DashboardPage passes a fallback empty butlers list, which would
    // otherwise compute genuine-looking zeros for the first three cells. Pending
    // approvals comes from a separate query, so mark it unavailable too here.
    const html = renderComponent({
      isError: true,
      pendingApprovalsAvailable: false,
      kpis: { totalButlers: 0, healthyButlers: 0, sessions24h: 0, pendingApprovals: 0 },
    });

    // No cell shows a literal 0; all four degrade to the em dash.
    expect(html).not.toContain(">0<");
    expect(html.match(/—/g)?.length).toBe(4);
  });
});
