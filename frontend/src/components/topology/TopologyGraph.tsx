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

import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";

interface ButlerNode {
  name: string;
  status: string;
  port: number;
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
}

const STATUS_COLORS: Record<string, string> = {
  ok: "#22c55e", // green-500
  online: "#22c55e",
  down: "#ef4444", // red-500
  offline: "#ef4444",
  degraded: "#eab308", // yellow-500
  stale: "#eab308", // yellow-500
};

function getStatusColor(status: string): string {
  return STATUS_COLORS[status] ?? "#6b7280"; // gray-500
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
      style: {
        background: getStatusColor(switchboard.status),
        color: "white",
        border: "2px solid #1e293b",
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
      style: {
        background: getStatusColor(heartbeat.status),
        color: "white",
        border: "2px dashed #64748b",
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
      style: {
        background: "#1e293b",
        color: "white",
        border: `2px solid ${getStatusColor(butler.status)}`,
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
      style: {
        background: "#0f172a",
        color: "white",
        border: `2px solid ${getStatusColor(connector.liveness)}`,
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
        style: { stroke: "#64748b" },
        animated: butler.status === "ok" || butler.status === "online",
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
        style: { stroke: "#8b5cf6" }, // purple for connector edges
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
        style: { stroke: "#94a3b8", strokeDasharray: "5 5" },
      });
    }
    // Heartbeat -> Switchboard
    if (hasSwitch) {
      edges.push({
        id: "hb-switchboard",
        source: "heartbeat",
        target: "switchboard",
        style: { stroke: "#94a3b8", strokeDasharray: "5 5" },
      });
    }
  }

  return edges;
}

export default function TopologyGraph({
  butlers,
  connectors = [],
  isLoading,
}: TopologyGraphProps) {
  const navigate = useNavigate();

  const nodes = useMemo(() => buildNodes(butlers, connectors), [butlers, connectors]);
  const edges = useMemo(() => buildEdges(butlers, connectors), [butlers, connectors]);

  const onNodeClick: NodeMouseHandler = useCallback(
    (_, node) => {
      if (node.id.startsWith("connector-")) {
        // connector-{type}-{identity} → /ingestion/connectors/{type}/{identity}
        const parts = node.id.replace("connector-", "").split("-");
        const connType = parts[0];
        const identity = parts.slice(1).join("-");
        navigate(`/ingestion/connectors/${connType}/${identity}`);
      } else {
        navigate(`/butlers/${node.id}`);
      }
    },
    [navigate],
  );

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Ecosystem Topology</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-96 animate-pulse rounded bg-muted" />
        </CardContent>
      </Card>
    );
  }

  if (butlers.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Ecosystem Topology</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex h-96 items-center justify-center text-sm text-muted-foreground">
            No butlers discovered
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Ecosystem Topology</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-96">
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
      </CardContent>
    </Card>
  );
}

export type { ButlerNode, ConnectorNode, TopologyGraphProps };
