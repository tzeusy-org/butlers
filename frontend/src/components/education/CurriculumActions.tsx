import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { TONE_COLORS } from "@/components/ui/StateDot";
import { useUpdateMindMapStatus } from "@/hooks/use-education";

const STATUS_TONE: Record<string, keyof typeof TONE_COLORS> = {
  draft: "amber",
  active: "green",
  completed: "green",
  abandoned: "neutral",
};

function statusBadgeStyle(status: string) {
  const tone = STATUS_TONE[status] ?? "green";
  return { borderColor: TONE_COLORS[tone], color: TONE_COLORS[tone] };
}

interface CurriculumActionsProps {
  mindMapId: string;
  status: string;
  nodeCount?: number;
}

export default function CurriculumActions({
  mindMapId,
  status,
  nodeCount,
}: CurriculumActionsProps) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pendingStatus, setPendingStatus] = useState<string | null>(null);
  const mutation = useUpdateMindMapStatus();

  function handleAction(newStatus: string) {
    setPendingStatus(newStatus);
    setConfirmOpen(true);
  }

  function handleConfirm() {
    if (pendingStatus) {
      mutation.mutate({ mindMapId, status: pendingStatus });
    }
    setConfirmOpen(false);
    setPendingStatus(null);
  }

  return (
    <div className="flex items-center gap-3">
      <Badge variant="outline" style={statusBadgeStyle(status)}>
        {status === "draft" ? "Setting up" : status}
      </Badge>

      {(status === "active" || status === "draft") && (
        <Button
          variant="outline"
          size="sm"
          onClick={() => handleAction("abandoned")}
          disabled={mutation.isPending}
        >
          Abandon
        </Button>
      )}
      {status === "abandoned" && (
        <Button
          variant="outline"
          size="sm"
          onClick={() => handleAction("active")}
          disabled={mutation.isPending || !nodeCount}
          title={!nodeCount ? "There are no concepts to return to." : undefined}
        >
          Re-activate
        </Button>
      )}

      {status === "abandoned" && !nodeCount && (
        <p className="text-sm text-muted-foreground">There are no concepts to return to.</p>
      )}
      {mutation.isError && (
        <p role="alert" className="text-sm text-destructive">
          {mutation.error instanceof Error ? mutation.error.message : "Could not change curriculum status."}
        </p>
      )}

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pendingStatus === "abandoned"
                ? "Abandon this curriculum?"
                : "Re-activate this curriculum?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingStatus === "abandoned"
                ? "Your progress will be preserved but the butler will stop scheduling reviews."
                : "The butler will resume scheduling reviews for this curriculum."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirm}>Confirm</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
