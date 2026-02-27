import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useQuizResponses } from "@/hooks/use-education";

const QUALITY_LABELS: Record<number, { label: string; className: string }> = {
  0: { label: "Blackout", className: "bg-red-100 text-red-800" },
  1: { label: "Wrong", className: "bg-red-100 text-red-800" },
  2: { label: "Hard", className: "bg-amber-100 text-amber-800" },
  3: { label: "Okay", className: "bg-yellow-100 text-yellow-800" },
  4: { label: "Good", className: "bg-blue-100 text-blue-800" },
  5: { label: "Easy", className: "bg-emerald-100 text-emerald-800" },
};

interface QuizHistoryListProps {
  mindMapId: string;
  nodeId?: string;
  compact?: boolean;
}

export default function QuizHistoryList({
  mindMapId,
  nodeId,
  compact,
}: QuizHistoryListProps) {
  const { data: responses } = useQuizResponses({
    mind_map_id: mindMapId,
    node_id: nodeId,
    limit: compact ? 5 : 20,
  });

  const items = responses?.data ?? [];

  if (items.length === 0) {
    const msg = compact
      ? "No quiz history yet"
      : "No quiz responses recorded for this curriculum yet.";
    return compact ? (
      <p className="text-xs text-muted-foreground">{msg}</p>
    ) : (
      <Card>
        <CardContent className="flex h-48 items-center justify-center text-muted-foreground">
          {msg}
        </CardContent>
      </Card>
    );
  }

  const list = (
    <div className="space-y-2">
      {items.map((r) => {
        const q = QUALITY_LABELS[r.quality] ?? QUALITY_LABELS[3];
        return (
          <div
            key={r.id}
            className="flex items-start justify-between gap-2 rounded-md border px-3 py-2"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm">{r.question_text}</p>
              {r.user_answer && (
                <p className="mt-0.5 truncate text-xs text-muted-foreground">
                  {r.user_answer}
                </p>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Badge className={q.className}>{q.label}</Badge>
              {!compact && (
                <span className="text-xs text-muted-foreground">
                  {new Date(r.responded_at).toLocaleDateString()}
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );

  if (compact) return list;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Quiz History</CardTitle>
      </CardHeader>
      <CardContent>{list}</CardContent>
    </Card>
  );
}
