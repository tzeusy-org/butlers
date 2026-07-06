/**
 * Truncate text to a maximum length, appending an ellipsis if needed.
 *
 * Shared by SessionTable and SessionsPinnedStrip (bu-ptaub) -- lives in its
 * own module (not a component file) so react-refresh/only-export-components
 * stays happy (component files here are not covered by the src/components/ui
 * override for that rule).
 */
export function truncate(text: string, max = 60): string {
  if (text.length <= max) return text;
  return text.slice(0, max) + "…";
}
