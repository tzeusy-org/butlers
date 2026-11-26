/**
 * The two mutually exclusive overlay panels of the calendar page: the Activity
 * audit log and the butler-extracted Proposals lane.
 */

import { useMemo, useState } from "react";

import {
  useAcceptCalendarProposal,
  useCalendarProposals,
  useCalendarWorkspaceAudit,
  useDismissCalendarProposal,
} from "@/hooks/use-calendar-workspace";
import type { CalendarPanel } from "@/pages/calendar/CalendarToolbar";

export const AUDIT_PAGE_SIZE = 50;

interface CalendarPanelsArgs {
  queryStart: string;
  queryEnd: string;
  timezone: string;
}

export function useCalendarPanels({ queryStart, queryEnd, timezone }: CalendarPanelsArgs) {
  const [activityOpen, setActivityOpen] = useState(false);
  const [proposalsOpen, setProposalsOpen] = useState(false);
  const [auditOffset, setAuditOffset] = useState(0);

  // The panels are mutually exclusive: opening one closes the other.
  function togglePanel(panel: CalendarPanel) {
    setActivityOpen(panel === "activity" ? (prev) => !prev : false);
    setProposalsOpen(panel === "proposals" ? (prev) => !prev : false);
    if (panel === "activity") setAuditOffset(0);
  }

  const auditQuery = useCalendarWorkspaceAudit(
    { limit: AUDIT_PAGE_SIZE, offset: auditOffset },
    { enabled: activityOpen },
  );

  // Proposals lane: butler-extracted candidate events awaiting accept/dismiss.
  // Cost: one extra proposals read per poll interval while the page is open (it was open-gated).
  // Always read, so the verdict can count what is waiting without the panel being open; the
  // panel lists the entries only while it is open. Accept/dismiss mutations reconcile the
  // optimistic lane state inside the panel.
  const proposalsQuery = useCalendarProposals({ start: queryStart, end: queryEnd, timezone });
  const proposalEntries = useMemo(
    () => (proposalsOpen ? (proposalsQuery.data?.data.entries ?? []) : []),
    [proposalsOpen, proposalsQuery.data?.data],
  );
  const acceptProposalMutation = useAcceptCalendarProposal();
  const dismissProposalMutation = useDismissCalendarProposal();

  // A dropped row means the lane is partial: its count could be short, so it is unknown, not low.
  const droppedRows = proposalsQuery.data?.data.proposals_dropped_rows;
  const proposalsPartial =
    proposalsQuery.isError ||
    proposalsQuery.data?.data.proposals_available === false ||
    droppedRows !== undefined && droppedRows > 0;

  return {
    activityOpen,
    proposalsOpen,
    auditOffset,
    setAuditOffset,
    togglePanel,
    auditQuery,
    proposalsQuery,
    proposalEntries,
    /** How many proposals wait in the window; `null` when the read failed or is unavailable. */
    proposalCount:
      proposalsPartial ? null
        : (proposalsQuery.data?.data.entries.length ?? null),
    proposalsLoading: proposalsQuery.isLoading,
    proposalsError: proposalsPartial,
    proposalsUnavailable: proposalsOpen && proposalsQuery.data?.data.proposals_available === false,
    proposalsDroppedRows: proposalsOpen ? proposalsQuery.data?.data.proposals_dropped_rows : undefined,
    acceptProposalMutation,
    dismissProposalMutation,
  };
}
