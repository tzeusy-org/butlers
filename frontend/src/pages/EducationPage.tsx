import { useState, useEffect, useMemo, useCallback } from "react";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { useMindMaps } from "@/hooks/use-education";
import MindMapGraph from "@/components/education/MindMapGraph";
import NodeDetailPanel from "@/components/education/NodeDetailPanel";
import CurriculumActions from "@/components/education/CurriculumActions";
import RequestCurriculumDialog from "@/components/education/RequestCurriculumDialog";
import CurriculumRequestReceiptPanel from "@/components/education/CurriculumRequestReceiptPanel";
import ReviewTimeline from "@/components/education/ReviewTimeline";
import MasterySummaryCards from "@/components/education/MasterySummaryCards";
import MasteryTrendChart from "@/components/education/MasteryTrendChart";
import CrossTopicChart from "@/components/education/CrossTopicChart";
import StrugglingNodesCard from "@/components/education/StrugglingNodesCard";
import QuizHistoryList from "@/components/education/QuizHistoryList";
import type { EducationNodeSelection } from "@/components/education/types";

export default function EducationPage() {
  const { data: mindMapsResponse, isLoading, isError, refetch } = useMindMaps({ status: "active" });
  const mindMaps = useMemo(() => mindMapsResponse?.data ?? [], [mindMapsResponse]);

  const [selectedMapId, setSelectedMapId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [requestDialogOpen, setRequestDialogOpen] = useState(false);
  // The `request_id` from the 202 (bu-6jv4m.10). Null falls the receipt panel
  // back to the latest request, so a reload still shows work in flight.
  const [trackedRequestId, setTrackedRequestId] = useState<string | null>(null);

  // Auto-select first mind map when data loads
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (mindMaps.length > 0 && !selectedMapId) {
      setSelectedMapId(mindMaps[0].id);
    }
  }, [mindMaps, selectedMapId]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const selectedMap = mindMaps.find((m) => m.id === selectedMapId) ?? null;

  const handleNodeSelection = useCallback((selection: EducationNodeSelection) => {
    setSelectedMapId(selection.mindMapId);
    setSelectedNodeId(selection.nodeId);
  }, []);

  const handleMindMapSelection = useCallback((mindMapId: string) => {
    setSelectedMapId(mindMapId);
    setSelectedNodeId(null);
  }, []);

  const receiptPanel = (
    <CurriculumRequestReceiptPanel
      requestId={trackedRequestId}
      onOpenCurriculum={handleMindMapSelection}
      onRetry={() => setRequestDialogOpen(true)}
    />
  );

  // Palette verb (bu-t64p2 -- reachability sweep, bu-qvnce.11 slice 5). The
  // one page-level action here; registered before any early return so the
  // hook always runs (it stays available in both the empty-state and the
  // full curriculum view, both of which render RequestCurriculumDialog).
  const educationCommands = useMemo<PaletteCommand[]>(
    () => [
      {
        id: "education-request-curriculum",
        label: "Request curriculum",
        keywords: ["new", "curriculum", "learning"],
        perform: () => setRequestDialogOpen(true),
      },
    ],
    [],
  );
  useRegisterCommands(educationCommands);

  if (isLoading) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold tracking-tight">Education</h1>
        <p className="text-muted-foreground">Loading...</p>
      </div>
    );
  }

  // An errored fetch must never fall through to the "No curriculums yet."
  // empty state — a killed backend would read as a calm all-clear (bu-mkd5r,
  // three-way loading/error/empty contract). Surface it as an honest
  // error-with-retry instead, above the empty branch below. Gate on an empty
  // cache so a background-refetch error keeps the last-good curriculum list
  // visible (React Query never clears data on error) rather than blanking a
  // populated page.
  if (isError && mindMaps.length === 0) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold tracking-tight">Education</h1>
        <div
          role="alert"
          className="flex flex-col items-start gap-3 py-8"
          data-testid="education-error"
        >
          <p className="text-sm text-destructive">
            Couldn't reach the education service. Retry.
          </p>
          <Button variant="outline" onClick={() => void refetch()}>
            Retry
          </Button>
        </div>
      </div>
    );
  }

  if (mindMaps.length === 0) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold tracking-tight">Education</h1>
        <EmptyState
          variant="page"
          title="No curriculums yet."
          description="Request one to start adaptive learning."
          action={
            <Button onClick={() => setRequestDialogOpen(true)}>
              Request curriculum
            </Button>
          }
        />
        {receiptPanel}
        <RequestCurriculumDialog
          open={requestDialogOpen}
          onOpenChange={setRequestDialogOpen}
          onAccepted={setTrackedRequestId}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Education</h1>
          <p className="text-muted-foreground mt-1">
            Adaptive learning dashboard: track mastery, review schedules, and curriculum progress.
          </p>
        </div>
        <Button onClick={() => setRequestDialogOpen(true)}>
          Request curriculum
        </Button>
      </div>

      {receiptPanel}

      {/* Mind map selector */}
      <Select value={selectedMapId ?? ""} onValueChange={handleMindMapSelection}>
        <SelectTrigger className="w-64">
          <SelectValue placeholder="Select a curriculum" />
        </SelectTrigger>
        <SelectContent>
          {mindMaps.map((m) => (
            <SelectItem key={m.id} value={m.id}>
              {m.title}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Tab panels */}
      <Tabs defaultValue="curriculum">
        <TabsList>
          <TabsTrigger value="curriculum">Curriculum</TabsTrigger>
          <TabsTrigger value="reviews">Reviews</TabsTrigger>
          <TabsTrigger value="analytics">Analytics</TabsTrigger>
        </TabsList>

        <TabsContent value="curriculum" className="space-y-4 pt-4">
          <MindMapGraph
            mindMapId={selectedMapId}
            onSelectNode={handleNodeSelection}
          />
          {selectedMap && (
            <CurriculumActions
              mindMapId={selectedMap.id}
              status={selectedMap.status}
            />
          )}
          {selectedMapId && (
            <QuizHistoryList mindMapId={selectedMapId} />
          )}
        </TabsContent>

        <TabsContent value="reviews" className="pt-4">
          <ReviewTimeline onSelectNode={handleNodeSelection} />
        </TabsContent>

        <TabsContent value="analytics" className="space-y-4 pt-4">
          <MasterySummaryCards mindMapId={selectedMapId} />
          <MasteryTrendChart mindMapId={selectedMapId} />
          <CrossTopicChart />
          <StrugglingNodesCard
            mindMapId={selectedMapId}
            onSelectNode={handleNodeSelection}
          />
        </TabsContent>
      </Tabs>

      {selectedMap && selectedNodeId && (
        <NodeDetailPanel
          mindMapId={selectedMap.id}
          nodeId={selectedNodeId}
          onClose={() => setSelectedNodeId(null)}
        />
      )}

      <RequestCurriculumDialog
        open={requestDialogOpen}
        onOpenChange={setRequestDialogOpen}
        onAccepted={setTrackedRequestId}
      />
    </div>
  );
}
