/**
 * Period selector buttons shared by Overview and Connectors tabs.
 */

import { Button } from "@/components/ui/button";
import type { IngestionPeriod } from "@/api/index.ts";

const PERIODS: Array<{ value: IngestionPeriod; label: string }> = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
];

interface PeriodSelectorProps {
  value: IngestionPeriod;
  onChange: (period: IngestionPeriod) => void;
}

export function PeriodSelector({ value, onChange }: PeriodSelectorProps) {
  return (
    <div className="flex gap-1">
      {PERIODS.map((p) => (
        <Button
          key={p.value}
          variant={value === p.value ? "secondary" : "ghost"}
          size="sm"
          onClick={() => onChange(p.value)}
        >
          {p.label}
        </Button>
      ))}
    </div>
  );
}
