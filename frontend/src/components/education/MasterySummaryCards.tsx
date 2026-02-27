import { Card, CardContent } from "@/components/ui/card";
import { useMasterySummary, useMindMapAnalytics } from "@/hooks/use-education";

interface MasterySummaryCardsProps {
  mindMapId: string | null;
}

export default function MasterySummaryCards({ mindMapId }: MasterySummaryCardsProps) {
  const { data: summary } = useMasterySummary(mindMapId);
  const { data: analytics } = useMindMapAnalytics(mindMapId);

  const estimatedDays = (analytics?.metrics?.estimated_completion_days as number) ?? null;

  if (!summary) {
    return (
      <div className="grid gap-4 sm:grid-cols-4">
        {[1, 2, 3, 4].map((i) => (
          <Card key={i}>
            <CardContent className="flex h-20 items-center justify-center text-muted-foreground">
              --
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  const cards = [
    { label: "Total Concepts", value: summary.total_nodes },
    { label: "Mastered", value: summary.mastered_count },
    {
      label: "Avg Mastery",
      value: `${Math.round(summary.avg_mastery_score * 100)}%`,
    },
    {
      label: "Est. Completion",
      value: estimatedDays != null ? `${estimatedDays}d` : "—",
    },
  ];

  return (
    <div className="grid gap-4 sm:grid-cols-4">
      {cards.map((card) => (
        <Card key={card.label}>
          <CardContent className="pt-4">
            <p className="text-sm text-muted-foreground">{card.label}</p>
            <p className="text-2xl font-bold">{card.value}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
