import { ExternalLinkIcon } from "lucide-react";
import { useNavigate } from "react-router";

import type { Citation } from "@/api/types.ts";
import { resolveShellCapability } from "@/lib/shell-capability";

export function CitationRow({ citations }: { citations: Citation[] }) {
  const navigate = useNavigate();
  if (citations.length === 0) return null;

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border/60 pt-2 font-sans text-xs">
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        Sources
      </span>
      {citations.map((citation, index) => {
        const key = `${citation.kind}:${citation.target ?? ""}:${citation.label}:${index}`;
        if (
          citation.kind === "internal" &&
          citation.target &&
          resolveShellCapability(citation.target)
        ) {
          return (
            <a
              key={key}
              href={citation.target}
              onClick={(event) => {
                event.preventDefault();
                navigate(citation.target as string);
              }}
              className="inline-flex min-h-11 items-center rounded-md px-2 text-foreground underline decoration-border-strong underline-offset-4 hover:bg-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
            >
              {citation.label}
            </a>
          );
        }
        if (citation.kind === "external" && citation.target?.startsWith("https://")) {
          return (
            <a
              key={key}
              href={citation.target}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-h-11 items-center gap-1 rounded-md px-2 text-foreground underline decoration-border-strong underline-offset-4 hover:bg-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
            >
              {citation.label}
              <ExternalLinkIcon aria-hidden="true" className="size-3" />
              <span className="sr-only">External link</span>
            </a>
          );
        }
        return (
          <span key={key} className="inline-flex min-h-11 items-center px-2 text-muted-foreground">
            {citation.label}
          </span>
        );
      })}
    </div>
  );
}
