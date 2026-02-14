import { format } from "date-fns";
import type { ApprovalRule } from "@/api/types";
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
import { Label } from "@/components/ui/label";
import { useRevokeRule } from "@/hooks/use-approvals";

interface RuleDetailDialogProps {
  rule: ApprovalRule | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function RuleDetailDialog({
  rule,
  open,
  onOpenChange,
}: RuleDetailDialogProps) {
  const revokeMutation = useRevokeRule();

  if (!rule) return null;

  function handleRevoke() {
    if (!rule) return;
    if (confirm("Are you sure you want to revoke this rule?")) {
      revokeMutation.mutate(rule.id, {
        onSuccess: () => {
          onOpenChange(false);
        },
      });
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Approval Rule Detail</DialogTitle>
          <DialogDescription>
            View details and constraints for this approval rule.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <Label className="text-muted-foreground">Status</Label>
            <div className="mt-1">
              <Badge variant={rule.active ? "default" : "outline"}>
                {rule.active ? "Active" : "Inactive"}
              </Badge>
            </div>
          </div>

          <div>
            <Label className="text-muted-foreground">Rule ID</Label>
            <p className="mt-1 font-mono text-sm">{rule.id}</p>
          </div>

          <div>
            <Label className="text-muted-foreground">Tool Name</Label>
            <p className="mt-1 font-mono text-sm">{rule.tool_name}</p>
          </div>

          <div>
            <Label className="text-muted-foreground">Description</Label>
            <p className="mt-1 text-sm">{rule.description}</p>
          </div>

          <div>
            <Label className="text-muted-foreground">Argument Constraints</Label>
            <pre className="mt-1 rounded-md bg-muted p-3 text-xs overflow-x-auto">
              {JSON.stringify(rule.arg_constraints, null, 2)}
            </pre>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label className="text-muted-foreground">Created At</Label>
              <p className="mt-1 text-sm">
                {format(new Date(rule.created_at), "PPpp")}
              </p>
            </div>

            {rule.expires_at && (
              <div>
                <Label className="text-muted-foreground">Expires At</Label>
                <p className="mt-1 text-sm">
                  {format(new Date(rule.expires_at), "PPpp")}
                </p>
              </div>
            )}

            <div>
              <Label className="text-muted-foreground">Use Count</Label>
              <p className="mt-1 text-sm">
                {rule.use_count}
                {rule.max_uses !== null ? ` / ${rule.max_uses}` : " (unlimited)"}
              </p>
            </div>

            {rule.created_from && (
              <div>
                <Label className="text-muted-foreground">Created From Action</Label>
                <p className="mt-1 text-sm font-mono">{rule.created_from}</p>
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          {rule.active && (
            <Button
              variant="destructive"
              onClick={handleRevoke}
              disabled={revokeMutation.isPending}
            >
              Revoke Rule
            </Button>
          )}
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
