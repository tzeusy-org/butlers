/**
 * "Whose calendar is this?" for one calendar source (REQ-module-calendar-037).
 *
 * An unclassified calendar counts as the owner's time only for events they are invited to, so the
 * control says so instead of leaving the assumption hidden. Choosing a role records it as the
 * owner's word; "Not set" clears it back to inference.
 */

import { toast } from "sonner";

import type { CalendarSourceRole, CalendarWorkspaceSourceFreshness } from "@/api/types.ts";
import { useSetCalendarSourceRole } from "@/hooks/use-calendar-workspace";
import { sourceName } from "@/pages/calendar/workspace-model";

const NOT_SET = "";

const ROLE_LABELS: Record<CalendarSourceRole, string> = {
  owner: "Mine",
  household_member: "Household member's",
  other_person: "Another person's",
  team: "Team's",
  subscription: "Subscription",
  holidays: "Holidays",
  birthdays: "Birthdays",
  butler: "Butlers calendar",
  unclassified: "Not set",
};

const CHOICES: CalendarSourceRole[] = [
  "owner",
  "household_member",
  "other_person",
  "team",
  "subscription",
  "holidays",
  "birthdays",
  "butler",
];

interface SourceRoleSelectProps {
  source: CalendarWorkspaceSourceFreshness;
}

export function SourceRoleSelect({ source }: SourceRoleSelectProps) {
  const mutation = useSetCalendarSourceRole();
  const role = source.time_role ?? "unclassified";
  const confirmed = source.role_basis === "owner_confirmed";
  const value = confirmed ? role : NOT_SET;
  const name = sourceName(source);

  function change(next: string) {
    mutation.mutate(
      { source_key: source.source_key, role: next === NOT_SET ? null : (next as CalendarSourceRole) },
      {
        onSuccess: (response) => {
          if (response.data.failed_butlers.length > 0) {
            toast.error(
              `Role saved, but not in: ${response.data.failed_butlers.join(", ")}. Try again.`,
            );
            return;
          }
          toast.success(`${name} updated`);
        },
        onError: (err) => toast.error(`Failed to set role: ${err.message}`),
      },
    );
  }

  return (
    <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <span className="sr-only">{`Whose calendar is ${name}`}</span>
      <select
        className="rounded border bg-background px-1.5 py-0.5 text-xs"
        value={value}
        disabled={mutation.isPending}
        onChange={(event) => change(event.target.value)}
      >
        <option value={NOT_SET}>
          {confirmed
            ? "Clear (go back to the assumed role)"
            : role === "unclassified"
              ? "Whose? Not set (counts only if invited)"
              : `${ROLE_LABELS[role]} (assumed)`}
        </option>
        {CHOICES.map((choice) => (
          <option key={choice} value={choice}>
            {ROLE_LABELS[choice]}
          </option>
        ))}
      </select>
    </label>
  );
}
