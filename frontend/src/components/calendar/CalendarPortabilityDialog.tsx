/**
 * Calendar data-portability dialog (bu-y16a8) — the FE surface for the ICS
 * export / subscribe / import backends (bu-8yi687 + bu-t2zxj).
 *
 * Three read/write-light affordances, all owner-sovereignty / anti-lock-in:
 *   1. Export .ics — one-shot download of the current view+range+filters
 *      (`GET /api/calendar/export/ics`). No provider write, no LLM.
 *   2. Subscribe — a copyable, read-only `webcal://…/subscribe.ics` feed an
 *      external calendar app re-fetches on its own schedule.
 *   3. Import .ics — upload a `.ics` into a calendar-enabled butler, deduped
 *      against existing entries (`POST /api/calendar/import/ics`). Surfaces the
 *      parsed / imported / skipped_duplicates counts; 413/400 surface as toasts.
 */

import { useState } from "react";
import { toast } from "sonner";

import {
  ApiError,
  calendarIcsExportUrl,
  calendarSubscribeUrl,
  calendarSubscribeWebcalUrl,
  importCalendarIcs,
} from "@/api/client.ts";
import type {
  CalendarIcsExportParams,
  CalendarIcsImportResponse,
  CalendarWorkspaceWritableCalendar,
} from "@/api/types.ts";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export interface CalendarPortabilityDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Export query — mirrors the workspace view/range/facet filters. */
  exportParams: CalendarIcsExportParams;
  /** Human label for the range being exported (e.g. "Feb 22 – Feb 28"). */
  rangeLabel: string;
  /** Calendar-enabled butler targets an `.ics` can be imported into. */
  importTargets: CalendarWorkspaceWritableCalendar[];
}

function targetLabel(target: CalendarWorkspaceWritableCalendar): string {
  const calendar = target.display_name || target.calendar_id;
  return target.butler_name ? `${target.butler_name} · ${calendar}` : calendar;
}

export function CalendarPortabilityDialog({
  open,
  onOpenChange,
  exportParams,
  rangeLabel,
  importTargets,
}: CalendarPortabilityDialogProps) {
  const subscribeUrl = calendarSubscribeUrl();
  const webcalUrl = calendarSubscribeWebcalUrl();

  const [file, setFile] = useState<File | null>(null);
  const [targetKey, setTargetKey] = useState<string>(
    importTargets[0]?.source_key ?? "",
  );
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<CalendarIcsImportResponse | null>(null);

  function handleExport() {
    const url = calendarIcsExportUrl(exportParams);
    const filename = `butlers-calendar-${exportParams.start.slice(0, 10)}-${exportParams.end.slice(0, 10)}.ics`;
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  }

  async function handleCopySubscribe() {
    try {
      await navigator.clipboard.writeText(subscribeUrl);
      toast.success("Subscribe URL copied");
    } catch {
      toast.error("Could not copy — select the URL and copy manually");
    }
  }

  async function handleImport() {
    if (!file) {
      toast.error("Choose a .ics file to import");
      return;
    }
    const target = importTargets.find((t) => t.source_key === targetKey);
    if (!target?.butler_name) {
      toast.error("Choose a calendar to import into");
      return;
    }
    setImporting(true);
    setResult(null);
    try {
      const response = await importCalendarIcs({
        file,
        butlerName: target.butler_name,
        calendarId: target.calendar_id,
      });
      setResult(response.data);
      toast.success(
        `Imported ${response.data.imported} · skipped ${response.data.skipped_duplicates} duplicate${
          response.data.skipped_duplicates === 1 ? "" : "s"
        }`,
      );
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : error instanceof Error
            ? error.message
            : "Import failed";
      toast.error(message);
    } finally {
      setImporting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Export / Import calendar</DialogTitle>
          <DialogDescription>
            Read-only ICS export and a live subscribe feed, plus deduped `.ics`
            import. Your calendar, your data — no lock-in.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-6 py-2">
          {/* Export */}
          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold text-[var(--fg)]">
              Export .ics
            </h3>
            <p className="text-xs text-[var(--dim)]">
              Download the current {exportParams.view} view for {rangeLabel} as a
              standard `.ics` file (matches the filters you have applied).
            </p>
            <div>
              <Button
                type="button"
                variant="outline"
                onClick={handleExport}
                aria-label="Download calendar as ICS"
              >
                Download .ics
              </Button>
            </div>
          </section>

          {/* Subscribe */}
          <section className="flex flex-col gap-2 border-t border-[var(--border)] pt-4">
            <h3 className="text-sm font-semibold text-[var(--fg)]">
              Subscribe (live feed)
            </h3>
            <p className="text-xs text-[var(--dim)]">
              Add this read-only URL to an external calendar app. It re-fetches a
              rolling window on its own schedule, so it always shows live state.
            </p>
            <div className="flex items-center gap-2">
              <Input
                readOnly
                value={subscribeUrl}
                aria-label="Subscribe feed URL"
                onFocus={(event) => event.currentTarget.select()}
                className="font-mono text-xs"
              />
              <Button
                type="button"
                variant="outline"
                onClick={handleCopySubscribe}
                aria-label="Copy subscribe URL"
              >
                Copy
              </Button>
            </div>
            <a
              href={webcalUrl}
              className="text-xs text-[var(--fg)] underline underline-offset-2 hover:text-[var(--dim)]"
            >
              Open in calendar app (webcal://)
            </a>
          </section>

          {/* Import */}
          <section className="flex flex-col gap-2 border-t border-[var(--border)] pt-4">
            <h3 className="text-sm font-semibold text-[var(--fg)]">
              Import .ics
            </h3>
            <p className="text-xs text-[var(--dim)]">
              Upload a `.ics` file into a calendar. Events already on the calendar
              are skipped, so re-importing the same file is a safe no-op.
            </p>
            {importTargets.length === 0 ? (
              <p className="text-xs text-[var(--dim)]">
                No writable calendar is available to import into. Connect a
                calendar-enabled butler first.
              </p>
            ) : (
              <>
                <div className="flex flex-col gap-1">
                  <Label htmlFor="ics-import-file" className="text-xs">
                    File
                  </Label>
                  <input
                    id="ics-import-file"
                    type="file"
                    accept=".ics,text/calendar"
                    aria-label="Choose .ics file"
                    onChange={(event) => {
                      setFile(event.target.files?.[0] ?? null);
                      setResult(null);
                    }}
                    className="text-xs text-[var(--fg)] file:mr-3 file:rounded file:border file:border-[var(--border)] file:bg-[var(--surface)] file:px-3 file:py-1.5 file:text-xs file:text-[var(--fg)]"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <Label htmlFor="ics-import-target" className="text-xs">
                    Import into
                  </Label>
                  <select
                    id="ics-import-target"
                    aria-label="Import target calendar"
                    value={targetKey}
                    onChange={(event) => setTargetKey(event.target.value)}
                    className="rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-xs text-[var(--fg)]"
                  >
                    {importTargets.map((target) => (
                      <option key={target.source_key} value={target.source_key}>
                        {targetLabel(target)}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <Button
                    type="button"
                    onClick={handleImport}
                    disabled={importing || !file}
                    aria-label="Import calendar file"
                  >
                    {importing ? "Importing…" : "Import"}
                  </Button>
                </div>
                {result ? (
                  <dl
                    aria-label="Import result"
                    className="mt-1 flex gap-4 text-xs text-[var(--fg)]"
                  >
                    <div>
                      <dt className="text-[var(--dim)]">Parsed</dt>
                      <dd className="tabular-nums">{result.parsed}</dd>
                    </div>
                    <div>
                      <dt className="text-[var(--dim)]">Imported</dt>
                      <dd className="tabular-nums">{result.imported}</dd>
                    </div>
                    <div>
                      <dt className="text-[var(--dim)]">Skipped duplicates</dt>
                      <dd className="tabular-nums">
                        {result.skipped_duplicates}
                      </dd>
                    </div>
                  </dl>
                ) : null}
              </>
            )}
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}
