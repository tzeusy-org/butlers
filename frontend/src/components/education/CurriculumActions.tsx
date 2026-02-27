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
import { useUpdateMindMapStatus } from "@/hooks/use-education";

const STATUS_BADGE: Record<string, string> = {
  active: "bg-emerald-100 text-emerald-800",
  completed: "bg-blue-100 text-blue-800",
  abandoned: "bg-gray-100 text-gray-800",
};

interface CurriculumActionsProps {
  mindMapId: string;
  status: string;
}

export default function CurriculumActions({
  mindMapId,
  status,
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
      <Badge className={STATUS_BADGE[status] ?? STATUS_BADGE.active}>
        {status}
      </Badge>

      {status === "active" && (
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
          disabled={mutation.isPending}
        >
          Re-activate
        </Button>
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
