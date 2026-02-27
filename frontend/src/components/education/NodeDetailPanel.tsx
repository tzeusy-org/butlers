import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { X } from "lucide-react";
import { useMindMap } from "@/hooks/use-education";
import QuizHistoryList from "./QuizHistoryList";

const STATUS_COLORS: Record<string, string> = {
  mastered: "bg-emerald-100 text-emerald-800",
  reviewing: "bg-blue-100 text-blue-800",
  learning: "bg-amber-100 text-amber-800",
  diagnosed: "bg-slate-100 text-slate-800",
  unseen: "bg-gray-100 text-gray-800",
};

interface NodeDetailPanelProps {
  mindMapId: string | null;
  nodeId: string | null;
  onClose: () => void;
}

export default function NodeDetailPanel({
  mindMapId,
  nodeId,
  onClose,
}: NodeDetailPanelProps) {
  const { data: mindMap } = useMindMap(mindMapId);
  const node = mindMap?.nodes?.find((n) => n.id === nodeId);

  if (!nodeId || !node) {
    return (
      <Card>
        <CardContent className="flex h-96 items-center justify-center text-muted-foreground">
          Click a node to view details
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between space-y-0">
        <div className="space-y-1">
          <CardTitle className="text-lg">{node.label}</CardTitle>
          <Badge className={STATUS_COLORS[node.mastery_status] ?? STATUS_COLORS.unseen}>
            {node.mastery_status}
          </Badge>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {node.description && (
          <p className="text-sm text-muted-foreground">{node.description}</p>
        )}

        <div className="grid grid-cols-2 gap-2 text-sm">
          <div>
            <span className="text-muted-foreground">Mastery</span>
            <p className="font-medium">{Math.round(node.mastery_score * 100)}%</p>
          </div>
          <div>
            <span className="text-muted-foreground">Ease Factor</span>
            <p className="font-medium">{node.ease_factor.toFixed(2)}</p>
          </div>
          <div>
            <span className="text-muted-foreground">Repetitions</span>
            <p className="font-medium">{node.repetitions}</p>
          </div>
          {node.effort_minutes != null && (
            <div>
              <span className="text-muted-foreground">Effort</span>
              <p className="font-medium">{node.effort_minutes} min</p>
            </div>
          )}
          {node.next_review_at && (
            <div className="col-span-2">
              <span className="text-muted-foreground">Next Review</span>
              <p className="font-medium">
                {new Date(node.next_review_at).toLocaleDateString()}
              </p>
            </div>
          )}
        </div>

        <div className="border-t pt-4">
          <h4 className="mb-2 text-sm font-medium">Quiz History</h4>
          <QuizHistoryList mindMapId={mindMapId!} nodeId={nodeId} compact />
        </div>
      </CardContent>
    </Card>
  );
}
