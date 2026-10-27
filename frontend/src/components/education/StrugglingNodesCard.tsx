import { Section, SectionContent, SectionHeader, SectionTitle } from "@/components/ui/Section";
import { Badge } from "@/components/ui/badge";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { useMindMapAnalytics } from "@/hooks/use-education";
import type { EducationNodeSelection } from "./types";

interface StrugglingNode {
  node_id: string;
  label: string;
  mastery_score: number;
  repetitions: number;
}

interface StrugglingNodesCardProps {
  mindMapId: string | null;
  onSelectNode: (selection: EducationNodeSelection) => void;
}

export default function StrugglingNodesCard({
  mindMapId,
  onSelectNode,
}: StrugglingNodesCardProps) {
  const { data: analytics, isError, refetch } = useMindMapAnalytics(mindMapId);

  const struggling = (analytics?.metrics?.struggling_nodes as StrugglingNode[]) ?? [];

  if (!mindMapId) return null;
  if (!isError && struggling.length === 0) return null;

  if (isError) {
    return (
      <Section>
        <SectionHeader>
          <SectionTitle>Struggling Concepts</SectionTitle>
        </SectionHeader>
        <SectionContent>
          <SourceDegradedNote
            label="Struggling concepts"
            detail="could not be reached"
            onRetry={() => void refetch()}
            testId="struggling-nodes-degraded"
          />
        </SectionContent>
      </Section>
    );
  }

  return (
    <Section>
      <SectionHeader>
        <SectionTitle>Struggling Concepts</SectionTitle>
      </SectionHeader>
      <SectionContent className="space-y-2">
        {struggling.map((node) => (
          <button
            key={node.node_id}
            type="button"
            className="flex w-full items-center justify-between rounded-md px-3 py-2 text-left hover:bg-muted"
            onClick={() => onSelectNode({ mindMapId, nodeId: node.node_id })}
          >
            <span className="text-sm font-medium">{node.label}</span>
            <span className="flex items-center gap-2">
              <Badge variant="outline" className="text-xs">
                {Math.round(node.mastery_score * 100)}%
              </Badge>
              <span className="text-xs text-muted-foreground">
                {node.repetitions} reps
              </span>
            </span>
          </button>
        ))}
      </SectionContent>
    </Section>
  );
}
