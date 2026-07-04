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
    // bu-86c4c.6: --green already resolves per-theme (see index.css .dark
    // block), so the raw Tailwind shade + dark: pair collapses to one token.
    rowClassName: "bg-[var(--green)]/10 text-[var(--green)]",
    testId: "qa-diff-line-plus",
  },
  "-": {
    sign: "-",
    rowClassName: "bg-[var(--red)]/10 text-[var(--red)]",
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
