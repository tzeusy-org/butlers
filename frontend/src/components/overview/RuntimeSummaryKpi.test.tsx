import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { RuntimeSummaryKpi } from "@/components/overview/RuntimeSummaryKpi";
import type { OverviewRuntimeKpis } from "./model";

const kpis: OverviewRuntimeKpis = {
  totalButlers: 4,
  healthyButlers: 3,
  sessions24h: 12,
  pendingApprovals: 2,
};

// Available cells render as a door (react-router <Link>, bu-27dxl.8.3), which
// requires a Router context even for renderToStaticMarkup.
function renderComponent(overrides: Partial<Parameters<typeof RuntimeSummaryKpi>[0]> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <RuntimeSummaryKpi
        kpis={kpis}
        isLoading={false}
        pendingApprovalsAvailable
        {...overrides}
      />
    </MemoryRouter>,
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

  it("degrades only Sessions when its board source is unavailable", () => {
    const html = renderComponent({ sessionsAvailable: false });

    expect(html).toContain(">4<");
    expect(html).toContain(">3<");
    expect(html).toContain(">2<");
    expect(html.match(/—/g)?.length).toBe(1);
    expect(html).toContain('class="sr-only"> unavailable</span>');
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

// ---------------------------------------------------------------------------
// KPI doors (bu-27dxl.8.3) -- supported destinations only, no fake filters,
// and unavailable dashes never carry a door.
// ---------------------------------------------------------------------------

describe("RuntimeSummaryKpi: KPI doors", () => {
  it("Total butlers routes to /butlers", () => {
    const html = renderComponent();
    expect(html).toContain('href="/butlers"');
  });

  it("Healthy routes to the SAME unfiltered /butlers board with an honest accessible label", () => {
    const html = renderComponent();
    // No fake "healthy-only" filter query string exists anywhere on this cell.
    expect(html).not.toMatch(/href="\/butlers\?[^"]*healthy/i);
    expect(html).toContain(
      'aria-label="Healthy butlers: opens the full unfiltered butler board"',
    );
  });

  it("Sessions routes to /sessions with the captured since/until window", () => {
    const html = renderComponent({
      sessionsSince: "2026-07-24T12:00:00.000Z",
      sessionsUntil: "2026-07-25T12:00:00.000Z",
    });
    expect(html).toContain(
      'href="/sessions?since=2026-07-24T12%3A00%3A00.000Z&amp;until=2026-07-25T12%3A00%3A00.000Z"',
    );
  });

  it("Sessions falls back to the plain /sessions door when no window was captured", () => {
    const html = renderComponent();
    expect(html).toContain('href="/sessions"');
  });

  it("Sessions source degradation removes only the Sessions door", () => {
    const html = renderComponent({ sessionsAvailable: false });

    expect(html).toContain('href="/butlers"');
    expect(html).toContain('href="/approvals"');
    expect(html).not.toContain('href="/sessions');
  });

  it("Pending approvals routes to /approvals, including when the count is a genuine zero", () => {
    const html = renderComponent({ kpis: { ...kpis, pendingApprovals: 0 } });
    expect(html).toContain('href="/approvals"');
  });

  it("no cell carries a door while the butler source is loading", () => {
    const html = renderComponent({ isLoading: true });
    expect(html).not.toContain("<a ");
  });

  it("no cell carries a door on butler-source error", () => {
    const html = renderComponent({
      isError: true,
      pendingApprovalsAvailable: false,
      kpis: { totalButlers: 0, healthyButlers: 0, sessions24h: 0, pendingApprovals: 0 },
    });
    expect(html).not.toContain("<a ");
  });

  it("only the approvals door disappears when approval metrics are unavailable", () => {
    const html = renderComponent({ pendingApprovalsAvailable: false });
    expect(html).toContain('href="/butlers"');
    expect(html).toContain('href="/sessions"');
    expect(html).not.toContain('href="/approvals"');
  });
});
