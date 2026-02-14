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

interface ActionTableProps {
  actions: ApprovalAction[];
  onActionClick: (action: ApprovalAction) => void;
}

function statusBadgeVariant(status: string) {
  switch (status) {
    case "pending":
      return "default";
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

export function ActionTable({ actions, onActionClick }: ActionTableProps) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Tool</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Requested</TableHead>
          <TableHead>Summary</TableHead>
          <TableHead>Session</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {actions.length === 0 ? (
          <TableRow>
            <TableCell colSpan={5} className="text-center text-muted-foreground">
              No actions found
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
                {formatDistanceToNow(new Date(action.requested_at), {
                  addSuffix: true,
                })}
              </TableCell>
              <TableCell className="max-w-md truncate text-sm">
                {action.agent_summary || "—"}
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {action.session_id ? action.session_id.slice(0, 8) : "—"}
              </TableCell>
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  );
}
