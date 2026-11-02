/**
 * Calendar workspace: an operator view over the user's provider calendars and
 * the butlers' scheduled tasks and reminders. Provider events are read-only
 * here (owner decision bu-kf3n4i = A); butlers and chat keep the calendar
 * writes through the calendar MCP tools. The page composes the data hooks in
 * `pages/calendar/` with the header, toolbar, canvas and dialogs.
 */

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";

import { CalendarAgendaView } from "@/components/calendar/CalendarAgendaView";
import { CalendarProposalsPanel } from "@/components/calendar/CalendarProposalsPanel";
import { CalendarResponsePanel } from "@/components/calendar/CalendarResponsePanel";
import { CalendarVerdictOpener } from "@/components/calendar/CalendarVerdictOpener";
import { DayBriefingCard } from "@/components/calendar/DayBriefingCard";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { HOUR_HEIGHT_PX } from "@/lib/calendar-grid";
import { CalendarActivityPanel } from "@/pages/calendar/CalendarActivityPanel";
import { CalendarCanvas } from "@/pages/calendar/CalendarCanvas";
import { CalendarConflictRadar } from "@/pages/calendar/CalendarConflictRadar";
import { CalendarEntryDetailPanel } from "@/pages/calendar/CalendarEntryDetailPanel";
import { CalendarHeader } from "@/pages/calendar/CalendarHeader";
import { CalendarSearchPalette } from "@/pages/calendar/CalendarSearchPalette";
import { CalendarToolbar } from "@/pages/calendar/CalendarToolbar";
import { ButlerEventDialog } from "@/pages/calendar/ButlerEventDialog";
import { QuickAddBar } from "@/pages/calendar/QuickAddBar";
import { SourcesDialog } from "@/pages/calendar/SourcesDialog";
import { useButlerEventEditor } from "@/pages/calendar/use-butler-event-editor";
import { useButlerLaneActions } from "@/pages/calendar/use-butler-lane-actions";
import { useCalendarCommands } from "@/pages/calendar/use-calendar-commands";
import { useCalendarContextData } from "@/pages/calendar/use-calendar-context-data";
import { AUDIT_PAGE_SIZE, useCalendarPanels } from "@/pages/calendar/use-calendar-panels";
import { useCalendarSync } from "@/pages/calendar/use-calendar-sync";
import {
  useCalendarUrlState,
  useSourceSelectionGuard,
} from "@/pages/calendar/use-calendar-url-state";
import { useCalendarWorkspaceData } from "@/pages/calendar/use-calendar-workspace-data";
import { useEntrySelection } from "@/pages/calendar/use-entry-selection";
import { useQuickAddCreate } from "@/pages/calendar/use-quick-add-create";
import { headlineLabel } from "@/pages/calendar/workspace-model";

/** Default scroll-to position (in hours) when the time grid first mounts. */
const DEFAULT_SCROLL_HOUR = 7.5;

export default function CalendarWorkspacePage() {
  const {
    view,
    range,
    anchor,
    selectedSourceKey,
    selectedCalendarId,
    selectedStatus,
    selectedSourceType,
    overlaysEnabled,
    updateQuery,
  } = useCalendarUrlState();
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

  const data = useCalendarWorkspaceData({
    view,
    range,
    anchor,
    timezone,
    selectedSourceKey,
    selectedCalendarId,
    selectedStatus,
    selectedSourceType,
  });
  const {
    metaQuery,
    connectedSources,
    freshnessPlaque,
    defaultTimezone,
    start,
    end,
    queryStart,
    queryEnd,
    userSources,
    sourceControls,
    workspaceQuery,
    entries,
    availableButlers,
  } = data;
  const context = useCalendarContextData({
    range,
    queryStart,
    queryEnd,
    timezone,
    overlaysEnabled,
    workspaceFetching: workspaceQuery.isFetching,
  });
  const panels = useCalendarPanels({ queryStart, queryEnd, timezone });
  const sync = useCalendarSync();
  const queryClient = useQueryClient();
  const selection = useEntrySelection({
    entries,
    workspaceFetching: workspaceQuery.isFetching,
    updateQuery,
  });
  const butlerEditor = useButlerEventEditor({ timezone, availableButlers });
  const { butlerMutation, openButlerCreateDialog } = butlerEditor;
  const laneActions = useButlerLaneActions(butlerMutation, defaultTimezone);
  const quickAdd = useQuickAddCreate({
    submittableCalendars: data.submittableCalendars,
    selectedSourceKey,
    defaultTimezone,
  });

  const [sourcesDialogOpen, setSourcesDialogOpen] = useState(false);
  const [agendaOpen, setAgendaOpen] = useState(false);
  const { selectedEntry, searchOpen, setSearchOpen } = selection;

  useSourceSelectionGuard({
    view,
    selectedSourceKey,
    selectedCalendarId,
    userSources,
    updateQuery,
  });
  useCalendarCommands({
    view,
    range,
    anchor,
    updateQuery,
    openButlerCreateDialog: () => openButlerCreateDialog(),
    openSearch: () => setSearchOpen(true),
  });

  // Scroll the time grid to the default hour when it mounts or the window moves.
  const timeGridRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if ((range === "week" || range === "day") && timeGridRef.current) {
      timeGridRef.current.scrollTop = DEFAULT_SCROLL_HOUR * HOUR_HEIGHT_PX;
    }
  }, [range, anchor]);

  const { invitationsQuery, overlaysQuery, dayBriefingQuery } = context;
  const dayBriefing = dayBriefingQuery.data?.data;
  const canvasHidden = panels.activityOpen || panels.proposalsOpen;

  return (
    <div className="flex h-full flex-col overflow-hidden" data-shell-scroll-owner="route">
      <CalendarHeader
        view={view}
        range={range}
        timezone={timezone}
        anchor={anchor}
        start={start}
        end={end}
        entryCount={entries.length}
        sync={sync}
        hiddenSourceCount={sourceControls.hiddenSources.size}
        butlerPending={butlerMutation.isPending}
        onSearch={() => setSearchOpen(true)}
        onAgenda={() => setAgendaOpen(true)}
        onSources={() => setSourcesDialogOpen(true)}
        onCreateButlerEvent={() => openButlerCreateDialog()}
      />

      <CalendarVerdictOpener
        entriesCount={entries.length}
        sourceCount={connectedSources.length}
        rangeLabel={range}
        workspaceLoading={workspaceQuery.isLoading}
        workspaceError={workspaceQuery.isError || !data.entriesSourceAvailable}
        sourceFreshnessLoading={metaQuery.isPending}
        sourceFreshnessError={
          metaQuery.isError || metaQuery.data?.data.sources_available === false
        }
        freshnessDetail={freshnessPlaque?.detail ?? null}
        conflictScanEnabled={context.radarEnabled}
        conflictLoading={context.conflictsQuery.isLoading}
        conflictError={context.conflictsQuery.isError}
        conflictsAvailable={context.conflictsAvailable}
        conflicts={context.conflictIssues}
        invitations={context.invitations}
        invitationsLoading={invitationsQuery.isPending || invitationsQuery.isPlaceholderData}
        invitationsRefreshing={invitationsQuery.isFetching}
        invitationsError={invitationsQuery.isError}
        invitationsAvailable={context.invitationsAvailable}
        invitationConflictsAvailable={context.invitationConflictsAvailable}
        invitationSourcesDegraded={context.invitationSourcesDegraded}
        invitationsHasMore={invitationsQuery.hasNextPage}
        invitationsLoadingMore={invitationsQuery.isFetchingNextPage}
        onMoreInvitations={() => {
          void invitationsQuery.fetchNextPage();
        }}
        onOpenInvitation={(entryId) => {
          void selection.openInvitation(entryId);
        }}
        invitationDetailLoading={selection.invitationDetailLoading}
      />

      <CalendarResponsePanel
        entries={context.invitations ?? []}
        onObserved={(receipt) => {
          if (receipt.status === "applied" && receipt.projection_available) {
            void queryClient.invalidateQueries({ queryKey: ["calendar-invitations"] });
            void queryClient.invalidateQueries({ queryKey: ["calendar-workspace"] });
            void queryClient.invalidateQueries({ queryKey: ["calendar-workspace-audit"] });
          }
        }}
        unavailable={
          !context.invitationsAvailable ||
          invitationsQuery.isPending ||
          invitationsQuery.isPlaceholderData
        }
      />

      {freshnessPlaque ? (
        <SourceDegradedNote
          testId="calendar-freshness-plaque"
          label="Calendar sync"
          detail={freshnessPlaque.detail}
          onRetry={sync.handleSyncAll}
          retryLabel={sync.syncButtonLabel}
          className="mb-4"
        />
      ) : null}

      <CalendarToolbar
        view={view}
        range={range}
        anchor={anchor}
        updateQuery={updateQuery}
        panels={{ activity: panels.activityOpen, proposals: panels.proposalsOpen }}
        onTogglePanel={panels.togglePanel}
        selectedStatus={selectedStatus}
        selectedSourceType={selectedSourceType}
        selectedCalendarId={selectedCalendarId}
        selectedSourceKey={selectedSourceKey}
        overlaysEnabled={overlaysEnabled}
        overlaysError={overlaysQuery.isError}
        overlaysFetched={overlaysQuery.isFetched}
        hasDomainContext={context.hasDomainContext}
        userSources={userSources}
        calendarFilterOptions={data.calendarFilterOptions}
      />

      {/* Natural-language quick-add (parse-then-confirm): the one provider-event
          write the dashboard still makes. User view only. */}
      {view === "user" ? (
        <div className="flex items-start border-b border-[var(--border)] py-3">
          <QuickAddBar
            timezone={defaultTimezone}
            butlerName={quickAdd.quickAddButlerName}
            disabled={!quickAdd.canCreateUserEvents}
            onConfirm={quickAdd.confirmQuickAddDraft}
          />
        </div>
      ) : null}

      {/* Day-briefing card ("tomorrow at a glance"), shown with the overlays layer. */}
      {overlaysEnabled ? (
        <div className="border-b border-[var(--border)] py-3">
          <DayBriefingCard
            heading={`Tomorrow · ${format(context.briefingDate, "EEE, MMM d")}`}
            isLoading={dayBriefingQuery.isLoading}
            isFetching={dayBriefingQuery.isFetching && !dayBriefingQuery.isLoading}
            isError={dayBriefingQuery.isError}
            error={dayBriefingQuery.error instanceof Error ? dayBriefingQuery.error : null}
            groups={dayBriefing?.groups ?? []}
            hasDomainContext={dayBriefing?.has_domain_context ?? false}
            hasEntries={dayBriefing?.has_entries ?? false}
            onSelectEntry={selection.handleBriefingSelect}
          />
        </div>
      ) : null}

      {panels.activityOpen ? (
        <div className="flex min-h-0 flex-1 flex-col pt-5">
          <CalendarActivityPanel
            auditQuery={panels.auditQuery}
            offset={panels.auditOffset}
            limit={AUDIT_PAGE_SIZE}
            onPageChange={panels.setAuditOffset}
          />
        </div>
      ) : null}

      {panels.proposalsOpen ? (
        <div className="flex min-h-0 flex-1 flex-col pt-5">
          <CalendarProposalsPanel
            entries={panels.proposalEntries}
            isLoading={panels.proposalsQuery.isLoading}
            isError={panels.proposalsQuery.isError}
            error={
              panels.proposalsQuery.error instanceof Error ? panels.proposalsQuery.error : null
            }
            acceptMutation={panels.acceptProposalMutation}
            dismissMutation={panels.dismissProposalMutation}
            timezone={defaultTimezone}
          />
        </div>
      ) : null}

      <div className={canvasHidden ? "hidden" : "flex min-h-0 flex-1"}>
        <CalendarCanvas
          view={view}
          range={range}
          data={data}
          context={context}
          overlaysEnabled={overlaysEnabled}
          laneActions={laneActions}
          butlerPending={butlerMutation.isPending}
          radar={
            <CalendarConflictRadar
              issues={context.conflictIssues}
              available={context.conflictsAvailable}
              conflictsQuery={context.conflictsQuery}
              acceptMutation={panels.acceptProposalMutation}
              dismissMutation={panels.dismissProposalMutation}
            />
          }
          timeGridRef={timeGridRef}
          onAddButlerEvent={openButlerCreateDialog}
          onOpenDetail={selection.openDetailPanel}
          updateQuery={updateQuery}
        />

        {selectedEntry ? (
          <aside
            data-testid="entry-detail-panel-aside"
            className="flex min-h-0 w-80 shrink-0 flex-col border-l border-[var(--border)] pl-5 pt-5"
          >
            <CalendarEntryDetailPanel
              key={selectedEntry.entry_id}
              entry={selectedEntry}
              onClose={selection.closeDetailPanel}
              butlerMutation={butlerMutation}
              timezone={defaultTimezone}
            />
          </aside>
        ) : null}
      </div>

      {searchOpen ? (
        <CalendarSearchPalette
          open={searchOpen}
          onOpenChange={setSearchOpen}
          view={view}
          timezone={timezone}
          displayTimezone={defaultTimezone}
          onJump={selection.handleSearchJump}
        />
      ) : null}

      {agendaOpen ? (
        <CalendarAgendaView
          entries={entries}
          rangeLabel={headlineLabel(range, start, end)}
          timezone={timezone}
          view={view === "butler" ? "butler" : "user"}
          onClose={() => setAgendaOpen(false)}
        />
      ) : null}

      <SourcesDialog
        open={sourcesDialogOpen}
        onOpenChange={setSourcesDialogOpen}
        connectedSources={connectedSources}
        metaLoading={metaQuery.isLoading}
        primaryCalendarId={data.primaryCalendarId}
        controls={sourceControls}
        sync={sync}
      />

      <ButlerEventDialog
        editor={butlerEditor}
        availableButlers={availableButlers}
        defaultTimezone={defaultTimezone}
      />
    </div>
  );
}
