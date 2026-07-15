import type { CalendarLinkedPerson } from "@/api/types.ts";
import { cn } from "@/lib/utils";

/** Initials mark for a person avatar — up to two leading name parts. */
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export interface LinkedPeopleAvatarsProps {
  people: CalendarLinkedPerson[];
  /** Max avatars to render before collapsing the rest into a "+N" chip. */
  max?: number;
  className?: string;
}

/**
 * Compact overlapping avatar cluster for the people linked to an existing
 * calendar event (bu-qs64f). Rendered on event pills so linked-people avatars
 * persist for existing events, not only at creation time in the dialog. The
 * full name list is exposed via the container `title` for hover/AT.
 *
 * Renders nothing when there are no linked people (never an empty box).
 */
export function LinkedPeopleAvatars({
  people,
  max = 3,
  className,
}: LinkedPeopleAvatarsProps) {
  if (people.length === 0) return null;

  const shown = people.slice(0, max);
  const overflow = people.length - shown.length;
  const fullList = people.map((p) => p.display_label).join(", ");

  return (
    <span
      data-testid="linked-people-avatars"
      className={cn("inline-flex shrink-0 items-center", className)}
      title={fullList}
      aria-label={`Linked people: ${fullList}`}
    >
      {shown.map((person, idx) => (
        <span
          key={person.entity_id}
          data-testid="linked-person-avatar"
          aria-hidden="true"
          className={cn(
            "inline-flex h-4 w-4 items-center justify-center rounded-full",
            "border border-bg bg-[var(--accent)]/15 text-[8px] font-semibold text-fg",
            idx > 0 && "-ml-1",
          )}
        >
          {initials(person.display_label)}
        </span>
      ))}
      {overflow > 0 ? (
        <span
          data-testid="linked-people-overflow"
          aria-hidden="true"
          className="-ml-1 inline-flex h-4 items-center justify-center rounded-full border border-bg bg-[var(--muted)] px-1 text-[8px] font-semibold text-[var(--mfg)]"
        >
          +{overflow}
        </span>
      ) : null}
    </span>
  );
}

export interface LinkedPeopleChipsProps {
  people: CalendarLinkedPerson[];
}

/**
 * Full labelled linked-people chips for the event detail panel (bu-qs64f),
 * mirroring the `ContactPeoplePicker` selected-chip style (initials avatar +
 * name) so the read surface matches the creation surface.
 *
 * Renders nothing when there are no linked people; the caller owns the section
 * heading and empty-state.
 */
export function LinkedPeopleChips({ people }: LinkedPeopleChipsProps) {
  if (people.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="linked-people-chips">
      {people.map((person) => (
        <span
          key={person.entity_id}
          data-testid="linked-person-chip"
          className="inline-flex items-center gap-1.5 rounded-[3px] border border-[var(--border-strong)] py-0.5 pl-1 pr-1.5 font-mono text-[11px] text-fg"
        >
          <span
            aria-hidden="true"
            className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-[var(--accent)]/15 text-[9px] font-semibold text-fg"
          >
            {initials(person.display_label)}
          </span>
          {person.display_label}
        </span>
      ))}
    </div>
  );
}
