import { cn } from "@/lib/utils";

export interface CounterEvidenceItem {
  hypothesis: string;
  verdict: "rejected" | "accepted" | "pending";
  reason: string;
}

interface CounterEvidenceProps {
  items: CounterEvidenceItem[];
  className?: string;
}

const verdictClassName: Record<CounterEvidenceItem["verdict"], string> = {
  accepted: "text-emerald-500",
  pending: "text-amber-500",
  rejected: "text-muted-foreground",
};

export function CounterEvidence({ items, className }: CounterEvidenceProps) {
  if (items.length === 0) return null;

  return (
    <div
      className={cn("divide-y divide-border/60 border-y border-border/60", className)}
      aria-label="Counter evidence"
    >
      {items.map((item, index) => (
        <div
          key={`${item.hypothesis}-${index}`}
          className="grid grid-cols-[1fr_auto] gap-3 py-2 font-mono text-[10px] leading-snug tnum"
        >
          <p className="min-w-0 text-muted-foreground">
            <span className="text-foreground">{item.hypothesis}</span> · {item.reason}
          </p>
          <p className={cn("uppercase tracking-[0.12em]", verdictClassName[item.verdict])}>
            {item.verdict}
          </p>
        </div>
      ))}
    </div>
  );
}
