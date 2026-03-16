import { formatDistanceToNow } from "date-fns";

import type { ApprovalAction } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface HistoryTableProps {
  actions: ApprovalAction[];
  onActionClick: (action: ApprovalAction) => void;
}

function statusBadgeVariant(status: string) {
  switch (status) {
    case "approved":
      return "secondary";
    case "executed":
      return "default";
    case "rejected":
      return "destructive";
    case "expired":
      return "outline";
    default:
      return "outline";
  }
}

export function HistoryTable({ actions, onActionClick }: HistoryTableProps) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Tool</TableHead>
          <TableHead>Outcome</TableHead>
          <TableHead>Decided</TableHead>
          <TableHead>Summary</TableHead>
          <TableHead>Decided By</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {actions.length === 0 ? (
          <TableRow>
            <TableCell colSpan={5} className="text-center text-muted-foreground">
              No resolved actions yet
            </TableCell>
          </TableRow>
        ) : (
          actions.map((action) => (
            <TableRow
              key={action.id}
              className="cursor-pointer hover:bg-muted/50"
              onClick={() => onActionClick(action)}
            >
              <TableCell className="font-medium">{action.tool_name}</TableCell>
              <TableCell>
                <Badge variant={statusBadgeVariant(action.status)}>
                  {action.status}
                </Badge>
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {action.decided_at
                  ? formatDistanceToNow(new Date(action.decided_at), { addSuffix: true })
                  : "—"}
              </TableCell>
              <TableCell className="max-w-md truncate text-sm">
                {action.agent_summary || "—"}
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {action.decided_by || (action.approval_rule_id ? "auto-rule" : "—")}
              </TableCell>
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  );
}
