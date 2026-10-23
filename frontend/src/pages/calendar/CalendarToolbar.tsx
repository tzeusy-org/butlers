/**
 * Calendar workspace toolbar: view and range pickers, previous/today/next,
 * the Activity/Proposals panel toggles, and the status,
 * type, overlays, calendar and source filters.
 */

import type { CalendarWorkspaceSourceFreshness, CalendarWorkspaceView } from "@/api/types.ts";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { StateDot } from "@/components/ui/StateDot";
import { cn } from "@/lib/utils";
import { PillButton } from "@/pages/calendar/atoms";
import type { UpdateCalendarQuery } from "@/pages/calendar/use-calendar-url-state";
import {
  RANGE_OPTIONS,
  SELECT_CLASS,
  SOURCE_TYPE_FACET_OPTIONS,
  STATUS_FACET_OPTIONS,
  VIEW_OPTIONS,
  shiftAnchor,
  sourceName,
  type CalendarRange,
} from "@/pages/calendar/workspace-model";

export type CalendarPanel = "activity" | "proposals";

const PANEL_BUTTONS: Array<{ panel: CalendarPanel; label: string }> = [
  { panel: "activity", label: "Activity" },
  { panel: "proposals", label: "Proposals" },
];

interface CalendarToolbarProps {
  view: CalendarWorkspaceView;
  range: CalendarRange;
  anchor: Date;
  updateQuery: UpdateCalendarQuery;
  panels: Record<CalendarPanel, boolean>;
  onTogglePanel: (panel: CalendarPanel) => void;
  selectedStatus: string;
  selectedSourceType: string;
  selectedCalendarId: string;
  selectedSourceKey: string;
  overlaysEnabled: boolean;
  overlaysError: boolean;
  overlaysFetched: boolean;
  hasDomainContext: boolean;
  userSources: CalendarWorkspaceSourceFreshness[];
  calendarFilterOptions: Array<{ calendarId: string; label: string }>;
}

export function CalendarToolbar({
  view,
  range,
  anchor,
  updateQuery,
  panels,
  onTogglePanel,
  selectedStatus,
  selectedSourceType,
  selectedCalendarId,
  selectedSourceKey,
  overlaysEnabled,
  overlaysError,
  overlaysFetched,
  hasDomainContext,
  userSources,
  calendarFilterOptions,
}: CalendarToolbarProps) {
  return (
  <div className="flex flex-wrap items-center gap-x-5 gap-y-3 border-y border-[var(--border)] py-3">
    <div className="flex items-center gap-2">
      <Eyebrow>View</Eyebrow>
      <div className="flex items-center gap-1">
        {VIEW_OPTIONS.map((option) => (
          <PillButton
            key={option.value}
            active={view === option.value}
            aria-pressed={view === option.value}
            onClick={() => updateQuery({ view: option.value })}
          >
            {option.label}
          </PillButton>
        ))}
      </div>
    </div>

    <div className="flex items-center gap-2">
      <Eyebrow>Range</Eyebrow>
      <div className="flex items-center gap-1">
        {RANGE_OPTIONS.map((option) => (
          <PillButton
            key={option.value}
            active={range === option.value}
            aria-pressed={range === option.value}
            onClick={() => updateQuery({ range: option.value })}
          >
            {option.label}
          </PillButton>
        ))}
      </div>
    </div>

    <div className="flex items-center gap-1">
      <PillButton
        aria-label="Previous range"
        onClick={() =>
          updateQuery({ anchor: shiftAnchor(anchor, range, -1) })
        }
      >
        ‹
      </PillButton>
      <PillButton
        aria-label="Jump to today"
        onClick={() => updateQuery({ anchor: new Date() })}
      >
        Today
      </PillButton>
      <PillButton
        aria-label="Next range"
        onClick={() =>
          updateQuery({ anchor: shiftAnchor(anchor, range, 1) })
        }
      >
        ›
      </PillButton>
    </div>

    {PANEL_BUTTONS.map(({ panel, label }) => (
      <PillButton
        key={panel}
        active={panels[panel]}
        aria-pressed={panels[panel]}
        onClick={() => onTogglePanel(panel)}
      >
        {label}
      </PillButton>
    ))}

    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 sm:ml-auto">
      <div className="flex items-center gap-2">
        <label
          htmlFor="status-filter"
          className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--mfg)]"
        >
          Status
        </label>
        <select
          id="status-filter"
          className={SELECT_CLASS}
          value={selectedStatus}
          onChange={(event) => updateQuery({ status: event.target.value })}
        >
          <option value="all">All statuses</option>
          {STATUS_FACET_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-2">
        <label
          htmlFor="type-filter"
          className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--mfg)]"
        >
          Type
        </label>
        <select
          id="type-filter"
          className={SELECT_CLASS}
          value={selectedSourceType}
          onChange={(event) => updateQuery({ kind: event.target.value })}
        >
          <option value="all">All types</option>
          {SOURCE_TYPE_FACET_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      <button
        type="button"
        role="switch"
        aria-checked={overlaysEnabled}
        aria-label="Cross-domain overlays"
        title="Domain overlays: finance bills/renewals, travel, relationship dates, health appointments"
        onClick={() => updateQuery({ overlays: !overlaysEnabled })}
        className={cn(
          "flex h-7 items-center gap-1.5 rounded-[3px] border px-2 font-mono text-[11px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
          overlaysEnabled
            ? "border-fg bg-foreground/[0.06] text-fg"
            : "border-[var(--border-strong)] text-[var(--mfg)] hover:text-fg",
        )}
      >
        <StateDot
          state={
            overlaysEnabled && overlaysError
              ? "error"
              : overlaysEnabled
                ? "ok"
                : "waiting"
          }
        />
        Overlays
        {overlaysEnabled && overlaysError ? (
          <span className="text-[var(--red-text)]">· unavailable</span>
        ) : overlaysEnabled && overlaysFetched && !hasDomainContext ? (
          <span className="text-[var(--dim)]">· none</span>
        ) : null}
      </button>

      {view === "user" ? (
        <>
          <div className="flex items-center gap-2">
            <label
              htmlFor="calendar-filter"
              className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--mfg)]"
            >
              Calendar
            </label>
            <select
              id="calendar-filter"
              className={SELECT_CLASS}
              value={selectedCalendarId}
              onChange={(event) =>
                updateQuery({ calendar: event.target.value, source: "all" })
              }
            >
              <option value="all">All calendars</option>
              {calendarFilterOptions.map((option) => (
                <option key={option.calendarId} value={option.calendarId}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2">
            <label
              htmlFor="source-filter"
              className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--mfg)]"
            >
              Source
            </label>
            <select
              id="source-filter"
              className={SELECT_CLASS}
              value={selectedSourceKey}
              onChange={(event) =>
                updateQuery({ source: event.target.value })
              }
            >
              <option value="all">All sources</option>
              {userSources
                .filter((source) =>
                  selectedCalendarId === "all"
                    ? true
                    : source.calendar_id === selectedCalendarId,
                )
                .map((source) => (
                  <option key={source.source_key} value={source.source_key}>
                    {sourceName(source)}
                  </option>
                ))}
            </select>
          </div>
        </>
      ) : null}
    </div>
  </div>
  );
}
