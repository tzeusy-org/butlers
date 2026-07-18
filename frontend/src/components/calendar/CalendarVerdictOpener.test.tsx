// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { CalendarVerdictOpener } from "@/components/calendar/CalendarVerdictOpener";

function render(overrides: Partial<React.ComponentProps<typeof CalendarVerdictOpener>> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <CalendarVerdictOpener
        entriesCount={14}
        sourceCount={3}
        rangeLabel="week"
        workspaceLoading={false}
        workspaceError={false}
        sourceFreshnessLoading={false}
        sourceFreshnessError={false}
        freshnessDetail={null}
        conflictScanEnabled={true}
        conflictLoading={false}
        conflictError={false}
        conflictsAvailable={true}
        conflicts={[]}
        {...overrides}
      />
    </MemoryRouter>,
  );
}

describe("CalendarVerdictOpener", () => {
  it("states a calm, conflict-checked week only when every source is available", () => {
    const html = render();

    expect(html).toContain("Quiet week: 14 events across 3 sources, no scheduling conflicts");
    expect(html).toContain("calendar-verdict-all-clear");
  });

  it("names an unavailable conflict scan instead of calling the week quiet", () => {
    const html = render({ conflictsAvailable: false });

    expect(html).toContain("calendar conflict scan unavailable");
    expect(html).not.toContain("calendar-verdict-all-clear");
  });

  it("adds a concrete conflict clause when the scan finds conflicts", () => {
    const html = render({ conflicts: [{} as never] });

    expect(html).toContain("1 scheduling conflict in view");
    expect(html).not.toContain("calendar-verdict-all-clear");
  });
});
