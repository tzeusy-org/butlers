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
  // Fetched only while the panel is open; accept/dismiss mutations reconcile the
  // optimistic lane state inside the panel.
  const proposalsQuery = useCalendarProposals(
    { start: queryStart, end: queryEnd, timezone },
    { enabled: proposalsOpen },
  );
  const proposalEntries = useMemo(
    () => (proposalsOpen ? (proposalsQuery.data?.data.entries ?? []) : []),
    [proposalsOpen, proposalsQuery.data?.data],
  );
  const acceptProposalMutation = useAcceptCalendarProposal();
  const dismissProposalMutation = useDismissCalendarProposal();

  return {
    activityOpen,
    proposalsOpen,
    auditOffset,
    setAuditOffset,
    togglePanel,
    auditQuery,
    proposalsQuery,
    proposalEntries,
    acceptProposalMutation,
    dismissProposalMutation,
  };
}
