import { useState } from "react";
import { format } from "date-fns";
import type { ApprovalAction } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  useApproveAction,
  useRejectAction,
} from "@/hooks/use-approvals";

interface ActionDetailDialogProps {
  action: ApprovalAction | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ActionDetailDialog({
  action,
  open,
  onOpenChange,
}: ActionDetailDialogProps) {
  const [rejectReason, setRejectReason] = useState("");
  const [createRule, setCreateRule] = useState(false);

  const approveMutation = useApproveAction();
  const rejectMutation = useRejectAction();

  if (!action) return null;

  const isPending = action.status === "pending";

  function handleApprove() {
    if (!action) return;
    approveMutation.mutate(
      { actionId: action.id, request: { create_rule: createRule } },
      {
        onSuccess: () => {
          onOpenChange(false);
          setCreateRule(false);
        },
      },
    );
  }

  function handleReject() {
    if (!action) return;
    rejectMutation.mutate(
      { actionId: action.id, request: { reason: rejectReason || undefined } },
      {
        onSuccess: () => {
          onOpenChange(false);
          setRejectReason("");
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Approval Action Detail</DialogTitle>
          <DialogDescription>
            Review and decide on this approval action.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <Label className="text-muted-foreground">Status</Label>
            <div className="mt-1">
              <Badge>{action.status}</Badge>
            </div>
          </div>

          <div>
            <Label className="text-muted-foreground">Tool Name</Label>
            <p className="mt-1 font-mono text-sm">{action.tool_name}</p>
          </div>

          <div>
            <Label className="text-muted-foreground">Tool Arguments</Label>
            <pre className="mt-1 rounded-md bg-muted p-3 text-xs overflow-x-auto">
              {JSON.stringify(action.tool_args, null, 2)}
            </pre>
          </div>

          {action.agent_summary && (
            <div>
              <Label className="text-muted-foreground">Agent Summary</Label>
              <p className="mt-1 text-sm">{action.agent_summary}</p>
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label className="text-muted-foreground">Requested At</Label>
              <p className="mt-1 text-sm">
                {format(new Date(action.requested_at), "PPpp")}
              </p>
            </div>

            {action.expires_at && (
              <div>
                <Label className="text-muted-foreground">Expires At</Label>
                <p className="mt-1 text-sm">
                  {format(new Date(action.expires_at), "PPpp")}
                </p>
              </div>
            )}

            {action.decided_by && (
              <div>
                <Label className="text-muted-foreground">Decided By</Label>
                <p className="mt-1 text-sm">{action.decided_by}</p>
              </div>
            )}

            {action.decided_at && (
              <div>
                <Label className="text-muted-foreground">Decided At</Label>
                <p className="mt-1 text-sm">
                  {format(new Date(action.decided_at), "PPpp")}
                </p>
              </div>
            )}

            {action.session_id && (
              <div>
                <Label className="text-muted-foreground">Session ID</Label>
                <p className="mt-1 text-sm font-mono">{action.session_id}</p>
              </div>
            )}

            {action.approval_rule_id && (
              <div>
                <Label className="text-muted-foreground">Rule ID</Label>
                <p className="mt-1 text-sm font-mono">{action.approval_rule_id}</p>
              </div>
            )}
          </div>

          {action.execution_result && (
            <div>
              <Label className="text-muted-foreground">Execution Result</Label>
              <pre className="mt-1 rounded-md bg-muted p-3 text-xs overflow-x-auto">
                {JSON.stringify(action.execution_result, null, 2)}
              </pre>
            </div>
          )}

          {isPending && (
            <div className="space-y-4 pt-4 border-t">
              <div className="flex items-center space-x-2">
                <Checkbox
                  id="create-rule"
                  checked={createRule}
                  onCheckedChange={(checked) => setCreateRule(checked === true)}
                />
                <Label htmlFor="create-rule" className="text-sm cursor-pointer">
                  Create standing rule from this action on approval
                </Label>
              </div>

              <div>
                <Label htmlFor="reject-reason">Rejection Reason (optional)</Label>
                <Textarea
                  id="reject-reason"
                  value={rejectReason}
                  onChange={(e) => setRejectReason(e.target.value)}
                  placeholder="Enter reason for rejection..."
                  className="mt-1"
                />
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          {isPending ? (
            <>
              <Button
                variant="destructive"
                onClick={handleReject}
                disabled={rejectMutation.isPending}
              >
                Reject
              </Button>
              <Button
                onClick={handleApprove}
                disabled={approveMutation.isPending}
              >
                Approve
              </Button>
            </>
          ) : (
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Close
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
