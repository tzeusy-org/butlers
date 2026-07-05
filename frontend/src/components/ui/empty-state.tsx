import * as React from "react";

import { Voice } from "@/components/ui/Voice";

interface EmptyStateProps {
  title: string;
  description: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
}

/**
 * Canonical empty-state surface — Dispatch Design Language § Voice Surface /
 * § Interface Copy: "Empty states are one serif-italic sentence with no
 * trailing explanation" and "no illustration."
 *
 * `title` renders as the one visible serif-italic sentence (the terse,
 * factual line — e.g. "No contacts found."). `description` is real
 * information many call sites still pass (context on why the surface is
 * empty or what fills it); rather than silently dropping it, it renders
 * `sr-only` so screen-reader users keep the elaboration while the visible
 * surface stays a single sentence. `icon` is intentionally never rendered —
 * icons are illustration, which the spec forbids on empty states.
 */
export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <Voice as="p" variant="italic" className="max-w-sm">
        {title}
      </Voice>
      {description && <span className="sr-only">{description}</span>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
