import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface AutoRefreshToggleProps {
  enabled: boolean;
  interval: number;
  onToggle: (enabled: boolean) => void;
  onIntervalChange: (interval: number) => void;
}

const INTERVAL_OPTIONS = [
  { value: 5_000, label: "5s" },
  { value: 10_000, label: "10s" },
  { value: 30_000, label: "30s" },
  { value: 60_000, label: "60s" },
] as const;

export function AutoRefreshToggle({
  enabled,
  interval,
  onToggle,
  onIntervalChange,
}: AutoRefreshToggleProps) {
  return (
    <div className="flex items-center gap-2">
      {enabled && (
        <Badge variant="default" className="bg-[var(--green)] text-white text-xs">
          Live
        </Badge>
      )}
      <Select
        value={String(interval)}
        onValueChange={(v) => onIntervalChange(Number(v))}
        disabled={!enabled}
      >
        <SelectTrigger className="h-8 w-[70px] text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {INTERVAL_OPTIONS.map((opt) => (
            <SelectItem key={opt.value} value={String(opt.value)}>
              {opt.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button
        variant={enabled ? "default" : "outline"}
        size="sm"
        className="h-8 text-xs"
        onClick={() => onToggle(!enabled)}
      >
        {enabled ? "Pause" : "Resume"}
      </Button>
    </div>
  );
}
