import { cn } from "@/lib/utils";

export interface DiffPreviewLine {
  kind: "meta" | "+" | "-" | " ";
  text: string;
}

interface DiffPreviewProps {
  lines: DiffPreviewLine[];
  className?: string;
}

const diffKindMeta = {
  " ": {
    sign: " ",
    rowClassName: "bg-transparent text-muted-foreground",
    testId: "qa-diff-line-context",
  },
  "+": {
    sign: "+",
    rowClassName: "bg-emerald-500/10 text-emerald-950 dark:text-emerald-100",
    testId: "qa-diff-line-plus",
  },
  "-": {
    sign: "-",
    rowClassName: "bg-red-500/10 text-red-950 dark:text-red-100",
    testId: "qa-diff-line-minus",
  },
  meta: {
    sign: "",
    rowClassName: "bg-muted text-muted-foreground",
    testId: "qa-diff-line-meta",
  },
} satisfies Record<DiffPreviewLine["kind"], { sign: string; rowClassName: string; testId: string }>;

export function DiffPreview({ lines, className }: DiffPreviewProps) {
  if (lines.length === 0) return null;

  return (
    <div
      className={cn(
        "overflow-x-auto border border-border/60 font-mono text-[11px] leading-relaxed tnum",
        className,
      )}
      aria-label="Diff preview"
    >
      {lines.map((line, index) => {
        const meta = diffKindMeta[line.kind];
        return (
          <div
            key={`${line.kind}-${index}-${line.text}`}
            className={cn("grid grid-cols-[24px_minmax(0,1fr)]", meta.rowClassName)}
            data-testid={meta.testId}
          >
            <span className="select-none px-2 text-right text-muted-foreground" aria-hidden="true">
              {meta.sign}
            </span>
            <span className="whitespace-pre px-2 py-0.5">{line.text}</span>
          </div>
        );
      })}
    </div>
  );
}
