import * as React from "react";

import { Voice } from "@/components/ui/Voice";

interface EmptyStateProps {
  title: string;
  description: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  /**
   * Which empty-state tier this call site belongs to (`about/heart-and-soul/
   * design-language.md` § Empty states draws this exact line):
   *
   * - `"page"` (default) — an ordinary page, panel, or table empty state.
   *   `title` renders as a heading; `description`, when passed, renders as
   *   ONE short visible sentence of context in muted color ("state the fact,
   *   then offer the next action"). This is the common case — audited across
   *   every current call site of this component (bu-eyo56), none of them are
   *   the stricter Voice-surface-inline case below, so `"page"` is the safe
   *   default and no call site needs to name it explicitly.
   * - `"voice"` — the strict Voice-surface-inline case: the briefing column,
   *   the attention list when nothing needs attention, the Next list when
   *   nothing is upcoming. Renders ONE serif-italic sentence with no visible
   *   explanation ("Nothing waiting."). `description`, when passed, still
   *   renders `sr-only` so screen-reader users keep the elaboration even
   *   though sighted users see one quiet line. Name this variant explicitly
   *   at every real Voice surface; do not rely on a default for it.
   */
  variant?: "page" | "voice";
}

/**
 * Canonical empty-state surface. Two tiers per the Dispatch Design Language
 * (`about/heart-and-soul/design-language.md` § Empty states,
 * `openspec/specs/dashboard-design-language/spec.md` § Interface Copy):
 * page-level empty states may show one short visible sentence of context;
 * only Voice-surface-inline empty states are held to the stricter "one
 * serif-italic sentence, no trailing explanation" rule. See the `variant`
 * prop for the exact split. `icon` is intentionally never rendered in either
 * tier — icons are illustration, which the spec forbids on empty states.
 */
export function EmptyState({
  title,
  description,
  action,
  variant = "page",
}: EmptyStateProps) {
  if (variant === "voice") {
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

  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <h2 className="text-lg font-medium">{title}</h2>
      {description && (
        <p className="mt-1 max-w-sm text-sm text-muted-foreground">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
