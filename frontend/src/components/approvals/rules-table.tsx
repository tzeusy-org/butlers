import { format } from "date-fns";
import type { ApprovalRule } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useRevokeRule } from "@/hooks/use-approvals";

interface RulesTableProps {
  rules: ApprovalRule[];
  onRuleClick: (rule: ApprovalRule) => void;
}

export function RulesTable({ rules, onRuleClick }: RulesTableProps) {
  const revokeMutation = useRevokeRule();

  function handleRevoke(e: React.MouseEvent, ruleId: string) {
    e.stopPropagation();
    if (confirm("Are you sure you want to revoke this rule?")) {
      revokeMutation.mutate(ruleId);
    }
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Tool</TableHead>
          <TableHead>Description</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Uses</TableHead>
          <TableHead>Created</TableHead>
          <TableHead>Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rules.length === 0 ? (
          <TableRow>
            <TableCell colSpan={6} className="text-center text-muted-foreground">
              No rules found
            </TableCell>
          </TableRow>
        ) : (
          rules.map((rule) => (
            <TableRow
              key={rule.id}
              className="cursor-pointer hover:bg-muted/50"
              onClick={() => onRuleClick(rule)}
            >
              <TableCell className="font-medium font-mono text-sm">
                {rule.tool_name}
              </TableCell>
              <TableCell className="max-w-md truncate">
                {rule.description}
              </TableCell>
              <TableCell>
                <Badge variant={rule.active ? "default" : "outline"}>
                  {rule.active ? "Active" : "Inactive"}
                </Badge>
              </TableCell>
              <TableCell className="text-sm">
                {rule.use_count}
                {rule.max_uses !== null ? ` / ${rule.max_uses}` : ""}
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {format(new Date(rule.created_at), "PP")}
              </TableCell>
              <TableCell>
                {rule.active && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={(e) => handleRevoke(e, rule.id)}
                    disabled={revokeMutation.isPending}
                  >
                    Revoke
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  );
}
