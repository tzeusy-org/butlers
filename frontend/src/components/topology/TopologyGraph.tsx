import { useCallback, useMemo } from "react";
import { useNavigate } from "react-router";
import {
  ReactFlow,
  Background,
  type Node,
  type Edge,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { Section, SectionContent, SectionHeader, SectionTitle } from "../ui/Section";
import { SourceDegradedNote } from "../ui/query-boundary";
import { stateColorVar } from "@/lib/visual-token-roles";
import type { CellTone } from "@/hooks/use-butler-status-board";

interface ButlerNode {
  name: string;
  status: string;
  /** Unused by the graph itself; kept optional for callers that already have it. */
  port?: number;
  /**
   * Canonical liveness tone from useButlerStatusBoard (bu-86c4c.17) -- the
   * SAME verdict rendered by the roster board and the heartbeat tile. When
   * present this wins over the legacy status-string color mapping below, so
   * a node here can never disagree with its row elsewhere on the dashboard.
   */
  tone?: CellTone;
}

interface ConnectorNode {
  connector_type: string;
  endpoint_identity: string;
  liveness: string; // "online" | "stale" | "offline"
}

interface TopologyGraphProps {
  butlers: ButlerNode[];
  connectors?: ConnectorNode[];
  isLoading?: boolean;
  /**
   * True when the connectors query errored (bu-86c4c.17 / #2873 review).
   * A failed connectors fetch must never render as an emptier map with no
   * explanation -- that is the exact "failure impersonates health" defect
   * the three-way loading/error/empty contract (query-boundary.tsx,
   * bu-86c4c.2) exists to prevent. `connectors` may still be `[]` or stale
   * cached data in this case; the degraded note names the source instead of
   * silently suppressing it.
   */
  connectorsError?: boolean;
}

const TONE_STATE = { green: "ok", amber: "degraded", red: "error", neutral: "waiting" } as const;
// Pattern and color share one border; the node shape continues to identify its role.
const STATE_BORDER = {
  ok: "2px solid",
  degraded: "2px dashed",
  error: "3px double",
  waiting: "2px dotted",
} as const;
const TONE_LABEL = { green: "running", amber: "overdue", red: "offline or quarantined", neutral: "idle or unknown" } as const;

function nodeState(status: string, tone?: CellTone) {
  if (tone) return TONE_STATE[tone];
  switch (status) {
    case "ok":
    case "online":
      return "ok";
    case "down":
    case "offline":
      return "error";
    case "degraded":
    case "stale":
      return "degraded";
    default:
      return "waiting";
  }
}

function stateBorder(status: string, tone?: CellTone): string {
  const state = nodeState(status, tone);
  return `${STATE_BORDER[state]} ${stateColorVar(state)}`;
}

function nodeStateLabel(status: string, tone?: CellTone): string {
  return tone ? TONE_LABEL[tone] : status;
}

function connectorLabel(c: ConnectorNode): string {
  // e.g. "gmail / user@example.com" — truncate long identities
  const id =
    c.endpoint_identity.length > 18
      ? c.endpoint_identity.slice(0, 16) + "…"
      : c.endpoint_identity;
  return `${c.connector_type}\n${id}`;
}

function buildNodes(
  butlers: ButlerNode[],
  connectors: ConnectorNode[] = [],
): Node[] {
  const nodes: Node[] = [];
  const switchboard = butlers.find((b) => b.name === "switchboard");
  const heartbeat = butlers.find((b) => b.name === "heartbeat");
  const others = butlers.filter(
    (b) => b.name !== "switchboard" && b.name !== "heartbeat",
  );

  // Center node: Switchboard
  const centerX = 300;
  const centerY = 250;

  if (switchboard) {
    nodes.push({
      id: switchboard.name,
      position: { x: centerX - 70, y: centerY - 20 },
      data: { label: switchboard.name },
      ariaRole: "link",
      ariaLabel: `${switchboard.name}: ${nodeStateLabel(switchboard.status, switchboard.tone)}`,
      style: {
        background: "var(--bg)",
        color: "var(--fg)",
        border: stateBorder(switchboard.status, switchboard.tone),
        borderRadius: "12px",
        padding: "16px 24px",
        fontWeight: 700,
        fontSize: "14px",
        width: 140,
        textAlign: "center" as const,
      },
    });
  }

  // Heartbeat node: top-right
  if (heartbeat) {
    nodes.push({
      id: heartbeat.name,
      position: { x: 550, y: 50 },
      data: { label: heartbeat.name },
      ariaRole: "link",
      ariaLabel: `${heartbeat.name}: ${nodeStateLabel(heartbeat.status, heartbeat.tone)}`,
      style: {
        background: "var(--bg)",
        color: "var(--fg)",
        border: stateBorder(heartbeat.status, heartbeat.tone),
        borderRadius: "50%",
        padding: "12px",
        fontWeight: 600,
        fontSize: "11px",
        width: 90,
        height: 90,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center" as const,
      },
    });
  }

  // Arrange butlers in a right semicircle (right side of switchboard)
  const butlerRadius = 200;
  const butlerCount = others.length;

  others.forEach((butler, i) => {
    // Spread from -PI/2 to PI/2 (right semicircle)
    const angle =
      -Math.PI / 2 + (Math.PI * (i + 0.5)) / Math.max(butlerCount, 1);
    const x = centerX + butlerRadius * Math.cos(angle) - 50;
    const y = centerY + butlerRadius * Math.sin(angle) - 20;

    nodes.push({
      id: butler.name,
      position: { x, y },
      data: { label: butler.name },
      ariaRole: "link",
      ariaLabel: `${butler.name}: ${nodeStateLabel(butler.status, butler.tone)}`,
      style: {
        background: "var(--bg)",
        color: "var(--fg)",
        border: stateBorder(butler.status, butler.tone),
        borderRadius: "8px",
        padding: "10px 16px",
        fontWeight: 500,
        fontSize: "12px",
        width: 120,
        textAlign: "center" as const,
      },
    });
  });

  // Arrange connectors in a left semicircle (left side of switchboard)
  const connectorRadius = 200;
  const connectorCount = connectors.length;

  connectors.forEach((connector, i) => {
    const connId = `connector-${connector.connector_type}-${connector.endpoint_identity}`;
    // Spread from PI/2 to 3PI/2 (left semicircle)
    const angle =
      Math.PI / 2 + (Math.PI * (i + 0.5)) / Math.max(connectorCount, 1);
    const x = centerX + connectorRadius * Math.cos(angle) - 55;
    const y = centerY + connectorRadius * Math.sin(angle) - 20;

    nodes.push({
      id: connId,
      position: { x, y },
      data: { label: connectorLabel(connector) },
      ariaRole: "link",
      ariaLabel: `${connector.connector_type} ${connector.endpoint_identity}: ${connector.liveness}`,
      style: {
        background: "var(--bg)",
        color: "var(--fg)",
        border: stateBorder(connector.liveness),
        borderRadius: "8px",
        padding: "8px 12px",
        fontWeight: 500,
        fontSize: "11px",
        width: 130,
        textAlign: "center" as const,
        whiteSpace: "pre-line" as const,
        lineHeight: "1.3",
      },
    });
  });

  return nodes;
}

function buildEdges(
  butlers: ButlerNode[],
  connectors: ConnectorNode[] = [],
): Edge[] {
  const edges: Edge[] = [];
  const hasSwitch = butlers.some((b) => b.name === "switchboard");
  const hasHeartbeat = butlers.some((b) => b.name === "heartbeat");
  const others = butlers.filter(
    (b) => b.name !== "switchboard" && b.name !== "heartbeat",
  );

  // Switchboard -> each butler
  if (hasSwitch) {
    for (const butler of others) {
      edges.push({
        id: `sw-${butler.name}`,
        source: "switchboard",
        target: butler.name,
        style: { stroke: "var(--dim)" },
        // Prefer the canonical tone when available: "green" means the
        // butler has an active session in progress (a stronger, more
        // meaningful animated-edge signal than mere MCP reachability).
        animated: butler.tone
          ? butler.tone === "green"
          : butler.status === "ok" || butler.status === "online",
      });
    }
  }

  // Connector -> Switchboard
  if (hasSwitch) {
    for (const connector of connectors) {
      const connId = `connector-${connector.connector_type}-${connector.endpoint_identity}`;
      edges.push({
        id: `conn-${connId}`,
        source: connId,
        target: "switchboard",
        style: { stroke: "var(--categorical-2)" },
        animated: connector.liveness === "online",
      });
    }
  }

  // Heartbeat -> each non-switchboard butler (dashed)
  if (hasHeartbeat) {
    for (const butler of others) {
      edges.push({
        id: `hb-${butler.name}`,
        source: "heartbeat",
        target: butler.name,
        style: { stroke: "var(--border-strong)", strokeDasharray: "5 5" },
      });
    }
    // Heartbeat -> Switchboard
    if (hasSwitch) {
      edges.push({
        id: "hb-switchboard",
        source: "heartbeat",
        target: "switchboard",
        style: { stroke: "var(--border-strong)", strokeDasharray: "5 5" },
      });
    }
  }

  return edges;
}

export default function TopologyGraph({
  butlers,
  connectors = [],
  isLoading,
  connectorsError = false,
}: TopologyGraphProps) {
  const navigate = useNavigate();

  const nodes = useMemo(() => buildNodes(butlers, connectors), [butlers, connectors]);
  const edges = useMemo(() => buildEdges(butlers, connectors), [butlers, connectors]);

  const openNode = useCallback((id: string) => {
    if (id.startsWith("connector-")) {
      const parts = id.replace("connector-", "").split("-");
      navigate(`/ingestion/connectors/${parts[0]}/${parts.slice(1).join("-")}`);
    } else {
      navigate(`/butlers/${id}`);
    }
  }, [navigate]);

  const onNodeClick: NodeMouseHandler = useCallback(
    (_, node) => openNode(node.id), [openNode],
  );

  if (isLoading) {
    return (
      <Section>
        <SectionHeader>
          <SectionTitle>Ecosystem Topology</SectionTitle>
        </SectionHeader>
        <SectionContent>
          <div className="h-96 rounded bg-muted" data-testid="topology-graph-skeleton" />
        </SectionContent>
      </Section>
    );
  }

  if (butlers.length === 0) {
    return (
      <Section>
        <SectionHeader>
          <SectionTitle>Ecosystem Topology</SectionTitle>
        </SectionHeader>
        <SectionContent>
          <div className="flex h-96 items-center justify-center text-sm text-muted-foreground">
            No butlers discovered
          </div>
        </SectionContent>
      </Section>
    );
  }

  return (
    <Section>
      <SectionHeader>
        <SectionTitle>Ecosystem Topology</SectionTitle>
      </SectionHeader>
      <SectionContent>
        {/* Legend -- the graph's border patterns and colors are otherwise unexplained; this
            names the one canonical liveness vocabulary shared with the
            roster board and heartbeat tile. */}
        <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-3 w-5" style={{ border: stateBorder("", "green") }} aria-hidden="true" />
            Running
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-3 w-5" style={{ border: stateBorder("", "neutral") }} aria-hidden="true" />
            Idle
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-3 w-5" style={{ border: stateBorder("", "amber") }} aria-hidden="true" />
            Overdue
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-3 w-5" style={{ border: stateBorder("", "red") }} aria-hidden="true" />
            Offline / Quarantined
          </span>
        </div>
        {/* Connectors-source degraded note -- a failed connectors fetch must
            never render as an emptier map with no explanation (#2873 review;
            three-way loading/error/empty contract, bu-86c4c.2). */}
        {connectorsError && (
          <SourceDegradedNote
            label="Connectors"
            detail="unavailable -- ingestion connector nodes may be missing"
            className="mb-2"
          />
        )}
        <div className="h-96" onKeyDownCapture={(event) => {
          // ReactFlow uses Enter/Space for selection but does not invoke onNodeClick.
          // Only activate the focused node itself, leaving graph pan/drag keys intact.
          const target = event.target as HTMLElement;
          if ((event.key === "Enter" || event.key === " ") && target.matches(".react-flow__node")) {
            const id = target.dataset.id;
            if (id) {
              event.preventDefault();
              event.stopPropagation();
              openNode(id);
            }
          }
        }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodeClick={onNodeClick}
            fitView
            proOptions={{ hideAttribution: true }}
            nodesDraggable={true}
            nodesConnectable={false}
          >
            <Background />
          </ReactFlow>
        </div>
      </SectionContent>
    </Section>
  );
}

export type { ButlerNode, ConnectorNode, TopologyGraphProps };
