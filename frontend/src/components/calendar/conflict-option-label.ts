import { format, parseISO } from "date-fns";

import type { ConflictIssue, ResolutionOption } from "@/api/types.ts";

/** The event an option acts on, by the entry id it names. */
function titleFor(issue: ConflictIssue, option: ResolutionOption): string {
  const entryId = option.rationale_fields.entry_id;
  return issue.events.find((e) => e.entry_id === entryId)?.title ?? "event";
}

function slotLabel(option: ResolutionOption): string {
  const start = option.rationale_fields.slot_start;
  if (typeof start !== "string") return "";
  try {
    return format(parseISO(start), "EEE HH:mm");
  } catch {
    return "";
  }
}

/** Plain-language label for an option; never more than the event's own title. */
export function optionLabel(issue: ConflictIssue, option: ResolutionOption): string {
  switch (option.kind) {
    case "move_block":
    case "move_own":
      return `Move ${titleFor(issue, option)} to ${slotLabel(option)}`;
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
