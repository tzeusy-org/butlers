import { useState, useEffect } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { useMindMaps } from "@/hooks/use-education";
import MindMapGraph from "@/components/education/MindMapGraph";
import NodeDetailPanel from "@/components/education/NodeDetailPanel";
import CurriculumActions from "@/components/education/CurriculumActions";
import RequestCurriculumDialog from "@/components/education/RequestCurriculumDialog";
import ReviewTimeline from "@/components/education/ReviewTimeline";
import MasterySummaryCards from "@/components/education/MasterySummaryCards";
import MasteryTrendChart from "@/components/education/MasteryTrendChart";
import CrossTopicChart from "@/components/education/CrossTopicChart";
import StrugglingNodesCard from "@/components/education/StrugglingNodesCard";
import QuizHistoryList from "@/components/education/QuizHistoryList";

export default function EducationPage() {
  const { data: mindMapsResponse, isLoading } = useMindMaps({ status: "active" });
  const mindMaps = mindMapsResponse?.data ?? [];

  const [selectedMapId, setSelectedMapId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [requestDialogOpen, setRequestDialogOpen] = useState(false);

  // Auto-select first mind map when data loads
  useEffect(() => {
    if (mindMaps.length > 0 && !selectedMapId) {
      setSelectedMapId(mindMaps[0].id);
    }
  }, [mindMaps, selectedMapId]);

  const selectedMap = mindMaps.find((m) => m.id === selectedMapId) ?? null;

  if (isLoading) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold tracking-tight">Education</h1>
        <p className="text-muted-foreground">Loading...</p>
      </div>
    );
  }

  if (mindMaps.length === 0) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold tracking-tight">Education</h1>
          <Button onClick={() => setRequestDialogOpen(true)}>
            Request New Curriculum
          </Button>
        </div>
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed p-12">
          <p className="text-muted-foreground text-center">
            No curriculums yet. Request one to get started with adaptive learning.
          </p>
        </div>
        <RequestCurriculumDialog
          open={requestDialogOpen}
          onOpenChange={setRequestDialogOpen}
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
            Adaptive learning dashboard — track mastery, review schedules, and curriculum progress.
          </p>
        </div>
        <Button onClick={() => setRequestDialogOpen(true)}>
          Request New Curriculum
        </Button>
      </div>

      {/* Mind map selector */}
      <Select value={selectedMapId ?? ""} onValueChange={setSelectedMapId}>
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
          <div className="grid gap-4 lg:grid-cols-[1fr_350px]">
            <MindMapGraph
              mindMapId={selectedMapId}
              onNodeClick={setSelectedNodeId}
            />
            <NodeDetailPanel
              mindMapId={selectedMapId}
              nodeId={selectedNodeId}
              onClose={() => setSelectedNodeId(null)}
            />
          </div>
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
          <ReviewTimeline />
        </TabsContent>

        <TabsContent value="analytics" className="space-y-4 pt-4">
          <MasterySummaryCards mindMapId={selectedMapId} />
          <MasteryTrendChart mindMapId={selectedMapId} />
          <CrossTopicChart />
          <StrugglingNodesCard
            mindMapId={selectedMapId}
            onNodeClick={setSelectedNodeId}
          />
        </TabsContent>
      </Tabs>

      <RequestCurriculumDialog
        open={requestDialogOpen}
        onOpenChange={setRequestDialogOpen}
      />
    </div>
  );
}
