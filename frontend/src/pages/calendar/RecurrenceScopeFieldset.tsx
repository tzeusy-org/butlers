import { Mono } from "@/components/ui/Mono";
import type { RecurrenceScope } from "@/pages/calendar/workspace-model";

/** Human-readable label for each recurrence scope option. */
const RECURRENCE_SCOPE_LABELS: Record<RecurrenceScope, string> = {
  this: "This occurrence",
  following: "This and following",
  series: "All events",
};

/**
 * Three-option recurrence scope chooser (this / following / series) shared by the
 * delete and edit confirmation sheets. The caller supplies the per-option impact
 * copy so the same control can describe a deletion or an edit.
 */
export function RecurrenceScopeFieldset({
  fieldsetTestId,
  optionPrefix,
  name,
  scope,
  onChange,
  impacts,
}: {
  fieldsetTestId: string;
  optionPrefix: string;
  name: string;
  scope: RecurrenceScope;
  onChange: (scope: RecurrenceScope) => void;
  impacts: Record<RecurrenceScope, string>;
}) {
  return (
    <fieldset
      data-testid={fieldsetTestId}
      className="flex flex-col gap-2 border-t border-[var(--border)] pt-3"
    >
      <legend className="sr-only">Recurrence scope</legend>
      {(["this", "following", "series"] as const).map((value) => (
        <label
          key={value}
          data-testid={`${optionPrefix}-${value}`}
          aria-label={RECURRENCE_SCOPE_LABELS[value]}
          className="flex cursor-pointer items-start gap-2 text-sm"
        >
          <input
            type="radio"
            name={name}
            value={value}
            checked={scope === value}
            onChange={() => onChange(value)}
            className="mt-1"
          />
          <span className="flex flex-col">
            <span className="font-medium">
              {RECURRENCE_SCOPE_LABELS[value]}
            </span>
            <Mono muted className="text-xs">
              {impacts[value]}
            </Mono>
          </span>
        </label>
      ))}
    </fieldset>
  );
}
