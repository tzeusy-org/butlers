import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useRequestCurriculum } from "@/hooks/use-education";

interface RequestCurriculumDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * Receives the `request_id` from the 202 (bu-6jv4m.10). The dialog closes on
   * acceptance, so without handing the id upward the page would have nothing to
   * track and the owner would be back to trusting a toast.
   */
  onAccepted?: (requestId: string) => void;
}

export default function RequestCurriculumDialog({
  open,
  onOpenChange,
  onAccepted,
}: RequestCurriculumDialogProps) {
  const [topic, setTopic] = useState("");
  const [goal, setGoal] = useState("");
  const mutation = useRequestCurriculum();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmedTopic = topic.trim();
    if (!trimmedTopic) return;

    mutation.mutate(
      { topic: trimmedTopic, goal: goal.trim() || undefined },
      {
        onSuccess: (accepted) => {
          setTopic("");
          setGoal("");
          onOpenChange(false);
          onAccepted?.(accepted.request_id);
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Request curriculum</DialogTitle>
            <DialogDescription>
              Tell the butler what you want to learn. Submitting records the
              request; the page then tracks it through to a curriculum or a
              named failure.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="topic">Topic</Label>
              <Input
                id="topic"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="e.g., Python, Linear Algebra, TCP/IP"
                maxLength={200}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="goal">Goal (optional)</Label>
              <Input
                id="goal"
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                placeholder="e.g., Learn web development with Flask"
                maxLength={500}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={!topic.trim() || mutation.isPending}>
              {mutation.isPending ? "Submitting..." : "Request"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
