import { Info } from "lucide-react";

import { cn } from "@/lib/utils";

interface ButlerManagedNoteProps {
  /** The kind of record this page shows, used in the default copy (e.g. "Medications"). */
  noun: string;
  className?: string;
}

/**
 * A small, non-interactive note clarifying that the data on a health page is
 * curated by the Health butler — not edited from the dashboard. These pages are
 * an observability surface: you log and update records by talking to the butler
 * (Telegram, MCP, etc.), and they appear here.
 *
 * This exists to keep the framing honest: the health pages are read-only views,
 * so we must not imply the user can add/edit/delete from the dashboard.
 */
export function ButlerManagedNote({ noun, className }: ButlerManagedNoteProps) {
  return (
    <div
      className={cn(
        "text-muted-foreground flex items-start gap-2 text-xs",
        className,
      )}
      data-testid="butler-managed-note"
    >
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span>
        {noun} are managed by the Health butler. This page is a read-only view —
        log or update records by talking to your butler, and they appear here.
      </span>
    </div>
  );
}
