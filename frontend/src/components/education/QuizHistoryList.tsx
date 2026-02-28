import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useQuizResponses } from "@/hooks/use-education";
import { ChevronDown, ChevronRight } from "lucide-react";

const QUALITY_LABELS: Record<number, { label: string; className: string }> = {
  0: { label: "Blackout", className: "bg-red-100 text-red-800" },
  1: { label: "Wrong", className: "bg-red-100 text-red-800" },
  2: { label: "Hard", className: "bg-amber-100 text-amber-800" },
  3: { label: "Okay", className: "bg-yellow-100 text-yellow-800" },
  4: { label: "Good", className: "bg-blue-100 text-blue-800" },
  5: { label: "Easy", className: "bg-emerald-100 text-emerald-800" },
};

const RESPONSE_TYPE_LABELS: Record<string, { label: string; className: string }> = {
  diagnostic: { label: "Diagnostic", className: "bg-purple-100 text-purple-800" },
  teach: { label: "Teach", className: "bg-sky-100 text-sky-800" },
  review: { label: "Review", className: "bg-slate-100 text-slate-800" },
};
const DIAGNOSTIC_PROBE_SENTINEL = "[diagnostic probe]";

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
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

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

  function toggleExpand(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function displayQuestionText(item: {
    question_text: string;
    response_type: string;
    node_label: string | null;
  }): string {
    const raw = item.question_text.trim();
    const isDiagnosticProbe = raw.toLowerCase() === DIAGNOSTIC_PROBE_SENTINEL;
    if (!isDiagnosticProbe && raw.length > 0) {
      return raw;
    }

    const typeLabel = RESPONSE_TYPE_LABELS[item.response_type]?.label ?? "Quiz";
    const nodeLabel = item.node_label?.trim();
    if (nodeLabel) {
      return `${typeLabel}: ${nodeLabel}`;
    }
    return typeLabel === "Diagnostic" ? "Diagnostic check" : `${typeLabel} prompt`;
  }

  const list = (
    <div className="space-y-2">
      {items.map((r) => {
        const q = QUALITY_LABELS[r.quality] ?? QUALITY_LABELS[3];
        const isExpanded = !compact && expanded.has(r.id);
        const rt = RESPONSE_TYPE_LABELS[r.response_type];
        const title = displayQuestionText(r);

        return (
          <div key={r.id} className="rounded-md border">
            <div
              className={`flex items-start justify-between gap-2 px-3 py-2${
                !compact ? " cursor-pointer hover:bg-muted/50" : ""
              }`}
              onClick={!compact ? () => toggleExpand(r.id) : undefined}
            >
              <div className="flex min-w-0 flex-1 items-start gap-2">
                {!compact && (
                  <span className="mt-0.5 shrink-0 text-muted-foreground">
                    {isExpanded ? (
                      <ChevronDown className="h-4 w-4" />
                    ) : (
                      <ChevronRight className="h-4 w-4" />
                    )}
                  </span>
                )}
                <p className="min-w-0 truncate text-sm">{title}</p>
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

            {isExpanded && (
              <div className="border-t bg-muted/30 px-3 py-2 text-sm">
                <div className="space-y-1.5">
                  {r.user_answer && (
                    <div>
                      <span className="font-medium text-muted-foreground">Answer: </span>
                      {r.user_answer}
                    </div>
                  )}
                  {r.evaluator_notes && (
                    <div>
                      <span className="font-medium text-muted-foreground">Evaluator: </span>
                      {r.evaluator_notes}
                    </div>
                  )}
                  <div className="flex flex-wrap gap-2">
                    {rt && <Badge className={rt.className}>{rt.label}</Badge>}
                    {r.node_label && (
                      <Badge variant="outline">{r.node_label}</Badge>
                    )}
                    <span className="text-xs text-muted-foreground">
                      Quality: {r.quality}/5
                    </span>
                  </div>
                </div>
              </div>
            )}
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
