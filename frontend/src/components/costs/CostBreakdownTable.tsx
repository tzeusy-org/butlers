import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../ui/table";
import { formatCostUsd as formatCost } from "@/lib/format-cost";

interface ButlerCostRow {
  name: string;
  cost: number;
  percentage: number;
}

interface CostBreakdownTableProps {
  byButler: Record<string, number>;
  totalCost: number;
  isLoading?: boolean;
}

function formatPercentage(value: number): string {
  if (value < 0.1) return "<0.1%";
  return `${value.toFixed(1)}%`;
}

export default function CostBreakdownTable({
  byButler,
  totalCost,
  isLoading,
}: CostBreakdownTableProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Cost by Butler</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-10 animate-pulse rounded bg-muted" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  const rows: ButlerCostRow[] = Object.entries(byButler)
    .map(([name, cost]) => ({
      name,
      cost,
      percentage: totalCost > 0 ? (cost / totalCost) * 100 : 0,
    }))
    .sort((a, b) => b.cost - a.cost);

  if (rows.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Cost by Butler</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">No cost data available</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Cost by Butler</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Butler</TableHead>
              <TableHead className="text-right">Cost</TableHead>
              <TableHead className="text-right">% of Total</TableHead>
              <TableHead className="w-24" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.name}>
                <TableCell className="font-medium">{row.name}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatCost(row.cost)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatPercentage(row.percentage)}
                </TableCell>
                <TableCell>
                  <div className="h-2 w-full rounded-full bg-muted">
                    <div
                      className="h-2 rounded-full bg-primary"
                      style={{ width: `${Math.min(row.percentage, 100)}%` }}
                    />
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
