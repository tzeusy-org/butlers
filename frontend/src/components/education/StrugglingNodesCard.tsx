import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useMindMapAnalytics } from "@/hooks/use-education";

interface StrugglingNode {
  node_id: string;
  label: string;
  mastery_score: number;
  repetitions: number;
}

interface StrugglingNodesCardProps {
  mindMapId: string | null;
  onNodeClick: (nodeId: string) => void;
}

export default function StrugglingNodesCard({
  mindMapId,
  onNodeClick,
}: StrugglingNodesCardProps) {
  const { data: analytics } = useMindMapAnalytics(mindMapId);

  const struggling = (analytics?.metrics?.struggling_nodes as StrugglingNode[]) ?? [];

  if (!mindMapId || struggling.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Struggling Concepts</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {struggling.map((node) => (
          <button
            key={node.node_id}
            type="button"
            className="flex w-full items-center justify-between rounded-md px-3 py-2 text-left hover:bg-muted"
            onClick={() => onNodeClick(node.node_id)}
          >
            <span className="text-sm font-medium">{node.label}</span>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-xs">
                {Math.round(node.mastery_score * 100)}%
              </Badge>
              <span className="text-xs text-muted-foreground">
                {node.repetitions} reps
              </span>
            </div>
          </button>
        ))}
      </CardContent>
    </Card>
  );
}
