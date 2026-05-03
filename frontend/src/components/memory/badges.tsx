import { Badge } from "@/components/ui/badge";

// eslint-disable-next-line react-refresh/only-export-components
export function permanenceBadge(p: string) {
  const colors: Record<string, string> = {
    permanent: "bg-blue-600 text-white hover:bg-blue-600/90",
    stable: "bg-sky-600 text-white hover:bg-sky-600/90",
    standard: "",
    volatile: "border-amber-500 text-amber-600",
    ephemeral: "border-red-500 text-red-500",
  };
  const cls = colors[p];
  if (!cls) return <Badge variant="secondary">{p}</Badge>;
  if (cls.startsWith("border-"))
    return (
      <Badge variant="outline" className={cls}>
        {p}
      </Badge>
    );
  return <Badge className={cls}>{p}</Badge>;
}

interface PercentageProgressBarProps {
  value: number;
  label?: string;
}

export function PercentageProgressBar({ value, label }: PercentageProgressBarProps) {
  const raw = Math.round(value * 100);
  const pct = Number.isNaN(raw) ? 0 : Math.min(100, Math.max(0, raw));
  const bar = (
    <div
      className="flex items-center gap-2"
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
    >
      <div className="bg-muted h-2 w-24 overflow-hidden rounded-full">
        <div
          className="bg-primary h-full rounded-full"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-muted-foreground text-sm">{pct}%</span>
    </div>
  );
  if (!label) return bar;
  return (
    <div>
      <p className="text-muted-foreground mb-1 text-xs font-medium">{label}</p>
      {bar}
    </div>
  );
}
