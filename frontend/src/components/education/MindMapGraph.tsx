import { useCallback, useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  type NodeMouseHandler,
  Handle,
  Position,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "@dagrejs/dagre";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useMindMap, useFrontierNodes } from "@/hooks/use-education";

const STATUS_COLORS: Record<string, string> = {
  mastered: "#10b981",
  reviewing: "#3b82f6",
  learning: "#f59e0b",
  diagnosed: "#64748b",
  unseen: "#d1d5db",
};

function ConceptNode({ data }: { data: Record<string, unknown> }) {
  const status = data.mastery_status as string;
  const color = STATUS_COLORS[status] ?? "#d1d5db";
  const isFrontier = data.is_frontier as boolean;
  const score = data.mastery_score as number;

  return (
    <div className="relative">
      {isFrontier && (
        <div
          className="absolute -inset-2 animate-pulse rounded-lg opacity-40"
          style={{ border: `2px solid ${color}` }}
        />
      )}
      <div
        className="rounded-lg border-2 bg-background px-3 py-2 text-center shadow-sm"
        style={{ borderColor: color }}
      >
        <Handle type="target" position={Position.Top} className="!bg-muted-foreground" />
        <div className="text-sm font-medium">{data.label as string}</div>
        <div className="text-xs text-muted-foreground">
          {Math.round(score * 100)}%
        </div>
        <Handle type="source" position={Position.Bottom} className="!bg-muted-foreground" />
      </div>
    </div>
  );
}

const nodeTypes = { concept: ConceptNode };

function layoutGraph(
  nodes: Node[],
  edges: Edge[],
): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "TB", ranksep: 80, nodesep: 60 });

  for (const node of nodes) {
    g.setNode(node.id, { width: 160, height: 60 });
  }
  for (const edge of edges) {
    g.setEdge(edge.source, edge.target);
  }

  dagre.layout(g);

  return {
    nodes: nodes.map((node) => {
      const pos = g.node(node.id);
      return {
        ...node,
        position: { x: pos.x - 80, y: pos.y - 30 },
      };
    }),
    edges,
  };
}

interface MindMapGraphProps {
  mindMapId: string | null;
  onNodeClick: (nodeId: string) => void;
}

export default function MindMapGraph({ mindMapId, onNodeClick }: MindMapGraphProps) {
  const { data: mindMap, isLoading } = useMindMap(mindMapId);
  const { data: frontierNodes } = useFrontierNodes(mindMapId);

  const frontierIds = useMemo(
    () => new Set((frontierNodes ?? []).map((n) => n.id)),
    [frontierNodes],
  );

  const { nodes, edges } = useMemo(() => {
    if (!mindMap?.nodes?.length) return { nodes: [], edges: [] };

    const rawNodes: Node[] = mindMap.nodes.map((n) => ({
      id: n.id,
      type: "concept",
      position: { x: 0, y: 0 },
      data: {
        label: n.label,
        mastery_status: n.mastery_status,
        mastery_score: n.mastery_score,
        is_frontier: frontierIds.has(n.id),
      },
    }));

    const rawEdges: Edge[] = mindMap.edges.map((e) => ({
      id: `${e.parent_node_id}-${e.child_node_id}`,
      source: e.parent_node_id,
      target: e.child_node_id,
      style: e.edge_type === "related" ? { strokeDasharray: "5 5" } : undefined,
      animated: false,
    }));

    return layoutGraph(rawNodes, rawEdges);
  }, [mindMap, frontierIds]);

  const handleNodeClick: NodeMouseHandler = useCallback(
    (_, node) => {
      onNodeClick(node.id);
    },
    [onNodeClick],
  );

  if (!mindMapId) return null;

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Concept Map</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex h-96 items-center justify-center text-muted-foreground">
            Loading...
          </div>
        </CardContent>
      </Card>
    );
  }

  if (nodes.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Concept Map</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex h-96 items-center justify-center text-muted-foreground">
            This curriculum has no concepts yet — the butler is still building it
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Concept Map</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-96">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodeClick={handleNodeClick}
            fitView
            proOptions={{ hideAttribution: true }}
          >
            <Background />
            <Controls />
          </ReactFlow>
        </div>
      </CardContent>
    </Card>
  );
}
