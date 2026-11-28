import type { ConflictIssue, ResolutionOption } from "@/api/types.ts";
import { formatOwnerDateTime } from "@/components/ui/time";

/** The event an option acts on, by the entry id it names. */
function titleFor(issue: ConflictIssue, option: ResolutionOption): string {
  const entryId = option.rationale_fields.entry_id;
  return issue.events.find((e) => e.entry_id === entryId)?.title ?? "event";
}

/** The slot in the zone it was compiled in (the owner's); no time when that zone is unknown. */
function slotLabel(issue: ConflictIssue, option: ResolutionOption): string {
  const start = option.rationale_fields.slot_start;
  if (typeof start !== "string" || !issue.owner_timezone) return "";
  return formatOwnerDateTime(start, issue.owner_timezone);
}

/** Plain-language label for an option; never more than the event's own title. */
export function optionLabel(issue: ConflictIssue, option: ResolutionOption): string {
  switch (option.kind) {
    case "move_block":
    case "move_own":
      return `Move ${titleFor(issue, option)} to ${slotLabel(issue, option)}`.trimEnd();
    case "decline":
      return `Decline ${titleFor(issue, option)}`;
    case "tentative":
      return `Mark ${titleFor(issue, option)} tentative`;
    case "ask_organizer":
      return `Ask the organizer of ${titleFor(issue, option)}`;
    case "keep_both":
      return "Keep both";
  }
}
