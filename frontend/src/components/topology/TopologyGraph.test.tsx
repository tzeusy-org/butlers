// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// TopologyGraph tests (bu-86c4c.17)
//
// @xyflow/react renders to a real canvas and requires browser APIs absent in
// jsdom/SSR, so it is mocked with a lightweight stand-in that renders each
// node's computed `style` as a plain DOM element -- enough to assert on the
// canonical liveness color mapping without a real canvas.
//
// Coverage:
//   - Loading skeleton / empty state
//   - Canonical tone coloring (green/amber/red/neutral) wins over the legacy
//     status-string mapping when `tone` is present
//   - Switchboard retains a neutral surface while state color stays on its
//     foreground and border
//   - Legend renders (colors are otherwise unexplained -- audit finding)
//   - Connectors-source degraded note (#2873 review): a failed connectors
//     fetch renders a named degraded note, never a silently emptier map
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import TopologyGraph from "./TopologyGraph";

const DASHBOARD_VISIBILITY_SPEC = readFileSync(
  resolve(process.cwd(), "../openspec/specs/dashboard-visibility/spec.md"),
  "utf8",
);

// ---------------------------------------------------------------------------
// Mock @xyflow/react -- render nodes as plain DOM elements carrying their
// computed style, so tone-driven colors are assertable via string matching.
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyNode = any;

vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ nodes, edges }: { nodes: AnyNode[]; edges: AnyNode[] }) => (
    <div data-testid="reactflow">
      {nodes.map((n) => (
        <div key={n.id} data-testid={`node-${n.id}`} style={n.style}>
          {n.data?.label}
        </div>
      ))}
      {edges.map((e: AnyNode) => (
        <div key={e.id} data-testid={`edge-${e.id}`} data-animated={String(!!e.animated)} />
      ))}
    </div>
  ),
  Background: () => null,
}));

function render(props: Parameters<typeof TopologyGraph>[0]): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <TopologyGraph {...props} />
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Loading / empty states
// ---------------------------------------------------------------------------

describe("TopologyGraph -- loading and empty states", () => {
  it("renders a loading skeleton when isLoading", () => {
    const html = render({ butlers: [], isLoading: true });
    expect(html).toContain('data-testid="topology-graph-skeleton"');
    expect(html).not.toContain('data-testid="reactflow"');
  });

  it("renders 'No butlers discovered' when the butler list is empty", () => {
    const html = render({ butlers: [] });
    expect(html).toContain("No butlers discovered");
  });
});

// ---------------------------------------------------------------------------
// Canonical liveness tone coloring
// ---------------------------------------------------------------------------

describe("TopologyGraph -- canonical liveness tone coloring", () => {
  it("colors a running butler green from its canonical tone", () => {
    const html = render({ butlers: [{ name: "finance", status: "ok", tone: "green" }] });
    expect(html).toContain("var(--green)");
  });

  it("colors an overdue butler amber from its canonical tone", () => {
    const html = render({ butlers: [{ name: "chronicler", status: "ok", tone: "amber" }] });
    expect(html).toContain("var(--amber)");
  });

  it("colors an offline/quarantined butler red from its canonical tone", () => {
    const html = render({ butlers: [{ name: "qa", status: "down", tone: "red" }] });
    expect(html).toContain("var(--red)");
  });

  it("colors an idle/unknown butler neutral gray from its canonical tone", () => {
    const html = render({ butlers: [{ name: "general", status: "ok", tone: "neutral" }] });
    expect(html).toContain("var(--dim)");
  });

  it("keeps Switchboard neutral while applying its canonical tone to foreground and border", () => {
    const html = render({
      butlers: [{ name: "switchboard", status: "ok", tone: "green" }],
    });
    const nodeMatch = html.match(/<div data-testid="node-switchboard"[^>]*>/);
    expect(nodeMatch).not.toBeNull();
    expect(nodeMatch![0]).toContain("background:var(--bg-deep)");
    expect(nodeMatch![0]).toContain("var(--green)");
    expect(nodeMatch![0]).toContain("border:2px solid var(--green)");
  });

  it("falls back to the legacy status-string color when tone is absent", () => {
    const html = render({ butlers: [{ name: "legacy-caller", status: "ok" }] });
    expect(html).toContain("var(--green)");
  });

  it("animates the switchboard edge only when the butler's tone is green (running)", () => {
    const html = render({
      butlers: [
        { name: "switchboard", status: "ok", tone: "green" },
        { name: "idle-butler", status: "ok", tone: "neutral" },
      ],
    });
    expect(html).toContain('data-testid="edge-sw-idle-butler" data-animated="false"');
  });
});

// ---------------------------------------------------------------------------
// Legend
// ---------------------------------------------------------------------------

describe("TopologyGraph -- legend", () => {
  it("renders a legend explaining the canonical liveness colors", () => {
    const html = render({ butlers: [{ name: "general", status: "ok", tone: "neutral" }] });
    expect(html).toContain("Running");
    expect(html).toContain("Overdue");
    expect(html).toContain("Offline");
    expect(html).not.toContain("Staffer");
  });

  it("keeps the topology requirement aligned with State Color Discipline", () => {
    expect(DASHBOARD_VISIBILITY_SPEC).toContain("neutral background");
    expect(DASHBOARD_VISIBILITY_SPEC).toContain("state-colored border and foreground");
    expect(DASHBOARD_VISIBILITY_SPEC).not.toContain(
      "Switchboard node's background is the status color",
    );
  });
});

// ---------------------------------------------------------------------------
// Connectors-source degraded note (#2873 review)
// ---------------------------------------------------------------------------

describe("TopologyGraph -- connectors-source degraded note", () => {
  it("renders a named degraded note when connectorsError is true, not a silently emptier map", () => {
    const html = render({
      butlers: [{ name: "general", status: "ok", tone: "neutral" }],
      connectors: [],
      connectorsError: true,
    });
    expect(html).toContain("Connectors");
    expect(html).toContain("unavailable");
    expect(html).toContain('role="alert"');
  });

  it("renders no degraded note when connectorsError is false", () => {
    const html = render({
      butlers: [{ name: "general", status: "ok", tone: "neutral" }],
      connectors: [],
      connectorsError: false,
    });
    expect(html).not.toContain('role="alert"');
  });

  it("still renders the graph itself alongside the degraded note (detect AND diagnose)", () => {
    const html = render({
      butlers: [{ name: "general", status: "ok", tone: "neutral" }],
      connectorsError: true,
    });
    expect(html).toContain('data-testid="reactflow"');
    expect(html).toContain('data-testid="node-general"');
  });
});
