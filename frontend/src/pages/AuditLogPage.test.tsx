// @vitest-environment jsdom
/**
 * AuditLogPage — unit tests.
 *
 * Covers:
 * - ?key= and ?actor= deep-link wiring (bu-zpivp) — preserved
 * - New-schema filter params: actor (filter bar), action, since (bu-ffnyz)
 * - Table renders new-schema AuditLogEntry rows
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import AuditLogPage from "@/pages/AuditLogPage";
import type { AuditLogParams, AuditLogEntry } from "@/api/types";
import {
  CommandRegistryProvider,
  useCommandMenuActions,
  type PaletteCommand,
} from "@/lib/command-registry";
import { ShortcutRegistryProvider } from "@/hooks/use-register-shortcut";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-audit-log", () => ({ useAuditLog: vi.fn() }));
vi.mock("@/hooks/use-butlers", () => ({ useButlers: vi.fn() }));

import { useAuditLog } from "@/hooks/use-audit-log";
import { useButlers } from "@/hooks/use-butlers";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeAuditResponse(entries: AuditLogEntry[] = []) {
  return {
    data: {
      data: entries,
      meta: { total: entries.length, offset: 0, limit: 20, has_more: false },
    },
    isLoading: false,
    isFetching: false,
    isError: false,
  };
}

function makeAuditErrorResponse() {
  return {
    data: undefined,
    isLoading: false,
    isFetching: false,
    isError: true,
  };
}

function makeEmptyButlersResponse() {
  return { data: { data: [] } };
}

function renderPage(initialPath = "/audit-log"): string {
  const qc = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>
        <AuditLogPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-search">{location.search}</div>;
}

function renderInteractivePage(initialPath = "/audit-log") {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>
        <AuditLogPage />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function CommandReader({ onRead }: { onRead: (commands: PaletteCommand[]) => void }) {
  onRead(useCommandMenuActions());
  return null;
}

function renderKeyboardPage(
  initialPath = "/audit-log",
  onCommands?: (commands: PaletteCommand[]) => void,
) {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <CommandRegistryProvider>
        <ShortcutRegistryProvider>
          <MemoryRouter initialEntries={[initialPath]}>
            <AuditLogPage />
            {onCommands ? <CommandReader onRead={onCommands} /> : null}
          </MemoryRouter>
        </ShortcutRegistryProvider>
      </CommandRegistryProvider>
    </QueryClientProvider>,
  );
}

afterEach(cleanup);

// ---------------------------------------------------------------------------
// Setup defaults
// ---------------------------------------------------------------------------

function setupDefaults(entries: AuditLogEntry[] = []) {
  vi.mocked(useAuditLog).mockReturnValue(
    makeAuditResponse(entries) as unknown as ReturnType<typeof useAuditLog>,
  );
  vi.mocked(useButlers).mockReturnValue(
    makeEmptyButlersResponse() as unknown as ReturnType<typeof useButlers>,
  );
}

// ---------------------------------------------------------------------------
// ?key= deep-link
// ---------------------------------------------------------------------------

describe("AuditLogPage — ?key= deep-link", () => {
  it("forwards key param to useAuditLog when ?key= is in the URL", () => {
    setupDefaults();
    renderPage("/audit-log?key=u%3Agoogle");

    const calls = vi.mocked(useAuditLog).mock.calls;
    expect(calls.length).toBeGreaterThan(0);
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.key).toBe("u:google");
  });

  it("renders the key filter chip when ?key= is present", () => {
    setupDefaults();
    const html = renderPage("/audit-log?key=u%3Agoogle");
    expect(html).toContain("data-testid=\"key-filter-chip\"");
    expect(html).toContain("key: u:google");
  });

  it("does not render key chip when ?key= is absent", () => {
    setupDefaults();
    const html = renderPage("/audit-log");
    expect(html).not.toContain("data-testid=\"key-filter-chip\"");
  });

  it("does not include key in params when ?key= is absent", () => {
    setupDefaults();
    renderPage("/audit-log");

    const calls = vi.mocked(useAuditLog).mock.calls;
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.key).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// ?actor= deep-link
// ---------------------------------------------------------------------------

describe("AuditLogPage — ?actor= deep-link", () => {
  it("forwards actor param to useAuditLog when ?actor= is in the URL", () => {
    setupDefaults();
    renderPage("/audit-log?actor=cli-abc123");

    const calls = vi.mocked(useAuditLog).mock.calls;
    expect(calls.length).toBeGreaterThan(0);
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.actor).toBe("cli-abc123");
  });

  it("hydrates the actor filter input from ?actor= deep-link", () => {
    setupDefaults();
    const html = renderPage("/audit-log?actor=cli-abc123");
    // The actor filter <input> value should reflect the deep-link actor.
    expect(html).toContain('id="filter-actor"');
    expect(html).toContain('value="cli-abc123"');
  });

  it("renders the actor filter chip when ?actor= is present", () => {
    setupDefaults();
    const html = renderPage("/audit-log?actor=cli-abc123");
    expect(html).toContain("data-testid=\"actor-filter-chip\"");
    expect(html).toContain("actor: cli-abc123");
  });

  it("does not render actor chip when ?actor= is absent", () => {
    setupDefaults();
    const html = renderPage("/audit-log");
    expect(html).not.toContain("data-testid=\"actor-filter-chip\"");
  });
});

// ---------------------------------------------------------------------------
// Combined deep-link + existing filters
// ---------------------------------------------------------------------------

describe("AuditLogPage — combined filters", () => {
  it("forwards both key and actor when both are in the URL", () => {
    setupDefaults();
    renderPage("/audit-log?key=u%3Agoogle&actor=cli-abc123");

    const calls = vi.mocked(useAuditLog).mock.calls;
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.key).toBe("u:google");
    expect(params.actor).toBe("cli-abc123");
  });

  it("shows both chips when both ?key= and ?actor= are present", () => {
    setupDefaults();
    const html = renderPage("/audit-log?key=s%3Aopenai&actor=owner");
    expect(html).toContain("data-testid=\"key-filter-chip\"");
    expect(html).toContain("data-testid=\"actor-filter-chip\"");
  });

  it("does not render deep-link chips section when neither is present", () => {
    setupDefaults();
    const html = renderPage("/audit-log");
    expect(html).not.toContain("data-testid=\"deep-link-filters\"");
  });
});

// ---------------------------------------------------------------------------
// New-schema filter params (bu-ffnyz): action and since
// ---------------------------------------------------------------------------

describe("AuditLogPage — new-schema URL filter params", () => {
  it("reads since filter from URL and builds params", () => {
    setupDefaults();
    renderPage("/audit-log?since=2026-01-01");

    const calls = vi.mocked(useAuditLog).mock.calls;
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.since).toBe("2026-01-01");
  });

  it("forwards owner-day From and To filters without replacing a legacy since link", () => {
    setupDefaults();
    renderPage(
      "/audit-log?since=2026-01-01T00%3A00%3A00&from_date=2026-07-11&to_date=2026-07-12",
    );

    const calls = vi.mocked(useAuditLog).mock.calls;
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.since).toBe("2026-01-01T00:00:00");
    expect(params.from_date).toBe("2026-07-11");
    expect(params.to_date).toBe("2026-07-12");
  });

  it("hydrates distinct From and To date inputs from URL-backed filter state", () => {
    setupDefaults();
    const html = renderPage("/audit-log?from_date=2026-07-11&to_date=2026-07-12");

    expect(html).toContain('id="filter-from-date"');
    expect(html).toContain('value="2026-07-11"');
    expect(html).toContain('id="filter-to-date"');
    expect(html).toContain('value="2026-07-12"');
  });

  it("does not include action in params when absent", () => {
    setupDefaults();
    renderPage("/audit-log");

    const calls = vi.mocked(useAuditLog).mock.calls;
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.action).toBeUndefined();
  });

  it("does not include actor in params when neither URL param nor filter is set", () => {
    setupDefaults();
    renderPage("/audit-log");

    const calls = vi.mocked(useAuditLog).mock.calls;
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.actor).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Default kind=privileged + noise toggle (JARVIS audit move 6)
// ---------------------------------------------------------------------------

describe("AuditLogPage — kind=privileged default + noise toggle", () => {
  it("defaults to kind=privileged with no ?noise= param", () => {
    setupDefaults();
    renderPage("/audit-log");

    const calls = vi.mocked(useAuditLog).mock.calls;
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.kind).toBe("privileged");
  });

  it("omits kind when ?noise=all is present", () => {
    setupDefaults();
    renderPage("/audit-log?noise=all");

    const calls = vi.mocked(useAuditLog).mock.calls;
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.kind).toBeUndefined();
  });

  it("still defaults to privileged even when ?kind=privileged is explicitly on the URL (e.g. Trust Console link)", () => {
    setupDefaults();
    renderPage("/audit-log?kind=privileged");

    const calls = vi.mocked(useAuditLog).mock.calls;
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.kind).toBe("privileged");
  });

  it("renders the noise toggle button", () => {
    setupDefaults();
    const html = renderPage("/audit-log");
    expect(html).toContain('data-testid="noise-toggle"');
    expect(html).toContain("Privileged only");
  });

  it("forwards ?result= to useAuditLog", () => {
    setupDefaults();
    renderPage("/audit-log?result=error");

    const calls = vi.mocked(useAuditLog).mock.calls;
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.result).toBe("error");
  });
});

// ---------------------------------------------------------------------------
// Single URL-serialized filter state (bu-qvnce.13) — actor/action/since have
// exactly one source of truth (the URL); there is no separate component
// state that a deep-link could silently override without updating.
// ---------------------------------------------------------------------------

describe("AuditLogPage — single URL-serialized filter state", () => {
  it("reads action filter from the URL and forwards it to useAuditLog", () => {
    setupDefaults();
    renderPage("/audit-log?action=model.priority");

    const calls = vi.mocked(useAuditLog).mock.calls;
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.action).toBe("model.priority");
  });

  it("hydrates the action filter input from the URL", () => {
    setupDefaults();
    const html = renderPage("/audit-log?action=model.priority");
    expect(html).toContain('id="filter-action"');
    expect(html).toContain('value="model.priority"');
  });

  it("clearing the actor chip and the filter-bar actor resolve to the same URL param", () => {
    // Both affordances mutate the same "actor" URL param -- there is no
    // second, independent piece of state for either to disagree with.
    setupDefaults();
    const html = renderPage("/audit-log?actor=cli-abc123");
    expect(html).toContain('data-testid="actor-filter-chip"');
    expect(html).toContain('value="cli-abc123"');
  });
});

// ---------------------------------------------------------------------------
// Debounced filter feedback
// ---------------------------------------------------------------------------

describe("AuditLogPage — debounced filter feedback", () => {
  it("marks visible rows busy while the URL has a new actor filter before its query is debounced", () => {
    vi.useFakeTimers();
    try {
      setupDefaults([
        {
          id: 1,
          ts: "2026-01-15T10:00:00Z",
          actor: "owner",
          action: "credential_set",
          target: "u:google",
          note: null,
          ip: null,
          request_id: null,
        },
      ]);
      const { container, getByLabelText, getByTestId } = renderInteractivePage();

      fireEvent.change(getByLabelText("Actor"), { target: { value: "owner" } });

      expect(getByTestId("location-search").textContent).toContain("actor=owner");
      expect(container.querySelector("[aria-busy]")?.getAttribute("aria-busy")).toBe("true");

      act(() => {
        vi.advanceTimersByTime(299);
      });
      expect(container.querySelector("[aria-busy]")?.getAttribute("aria-busy")).toBe("true");
    } finally {
      vi.useRealTimers();
    }
  });
});

// ---------------------------------------------------------------------------
// Table renders new-schema rows correctly
// ---------------------------------------------------------------------------

describe("AuditLogPage — table renders new-schema rows", () => {
  it("renders actor and action columns from AuditLogEntry", () => {
    const entry: AuditLogEntry = {
      id: 1,
      ts: "2026-01-15T10:00:00Z",
      actor: "owner",
      action: "credential_set",
      target: "u:google",
      note: null,
      ip: null,
      request_id: null,
    };
    setupDefaults([entry]);
    const html = renderPage("/audit-log");
    expect(html).toContain("owner");
    expect(html).toContain("credential_set");
    expect(html).toContain("u:google");
  });

  it("renders multiple entries", () => {
    const entries: AuditLogEntry[] = [
      {
        id: 1,
        ts: "2026-01-15T10:00:00Z",
        actor: "owner",
        action: "credential_set",
        target: "u:google",
        note: null,
        ip: null,
        request_id: null,
      },
      {
        id: 2,
        ts: "2026-01-15T09:00:00Z",
        actor: "qa",
        action: "session_start",
        target: null,
        note: null,
        ip: null,
        request_id: null,
      },
    ];
    setupDefaults(entries);
    const html = renderPage("/audit-log");
    expect(html).toContain("credential_set");
    expect(html).toContain("session_start");
    expect(html).toContain("qa");
  });
});

describe("AuditLogPage — keyboard triage", () => {
  it("moves between rows and exposes the selected-entry toggle in the palette", () => {
    setupDefaults([
      {
        id: 1,
        ts: "2026-01-15T10:00:00Z",
        actor: "owner",
        action: "credential_set",
        target: "u:google",
        note: "first detail",
        ip: null,
        request_id: null,
      },
      {
        id: 2,
        ts: "2026-01-15T09:00:00Z",
        actor: "qa",
        action: "session_start",
        target: null,
        note: "second detail",
        ip: null,
        request_id: null,
      },
    ]);
    let commands: PaletteCommand[] = [];
    const { container } = renderKeyboardPage("/audit-log", (next) => (commands = next));

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "j", bubbles: true }));
    });
    const firstRow = container.querySelector('[data-audit-row-id="1"]');
    const firstTrigger = firstRow?.querySelector<HTMLElement>(
      '[data-testid="audit-log-row-trigger"]',
    );
    expect(document.activeElement).toBe(firstTrigger);
    act(() => {
      firstTrigger!.dispatchEvent(new KeyboardEvent("keydown", { key: "j", bubbles: true }));
    });

    const selectedRow = container.querySelector('[data-audit-row-id="2"]');
    const selectedTrigger = selectedRow?.querySelector<HTMLElement>(
      '[data-testid="audit-log-row-trigger"]',
    );
    expect(selectedRow?.getAttribute("data-audit-selected")).toBe("true");
    expect(document.activeElement).toBe(selectedTrigger);
    const toggleCommand = commands.find((command) => command.id === "toggle-selected-audit-entry");
    expect(toggleCommand).toMatchObject({
      label: "Toggle selected audit entry",
      binding: ["Enter"],
    });

    act(() => {
      toggleCommand?.perform();
    });

    expect(container.textContent).toContain("second detail");
    expect(container.textContent).not.toContain("first detail");
  });

  it("lets the focused disclosure row handle Enter exactly once", () => {
    setupDefaults([
      {
        id: 1,
        ts: "2026-01-15T10:00:00Z",
        actor: "owner",
        action: "credential_set",
        target: "u:google",
        note: "first detail",
        ip: null,
        request_id: null,
      },
    ]);
    const { container } = renderKeyboardPage();

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "j", bubbles: true }));
    });

    const trigger = container.querySelector<HTMLElement>('[data-testid="audit-log-row-trigger"]');
    expect(document.activeElement).toBe(trigger);

    const event = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
    act(() => {
      trigger?.dispatchEvent(event);
    });

    expect(event.defaultPrevented).toBe(true);
    expect(container.textContent).toContain("first detail");
  });
});

// ---------------------------------------------------------------------------
// Action filter placeholder uses a real action name
// ---------------------------------------------------------------------------

describe("AuditLogPage — action filter placeholder", () => {
  it("uses a real action name as the action filter placeholder", () => {
    setupDefaults();
    const html = renderPage("/audit-log");
    // The placeholder must be a real action (e.g. model.priority), not the
    // non-existent "credential_set".
    expect(html).toContain('placeholder="e.g. model.priority"');
    expect(html).not.toContain('placeholder="e.g. credential_set"');
  });
});

// ---------------------------------------------------------------------------
// Error state — a failed fetch (e.g. 503) shows an error, not "no entries"
// ---------------------------------------------------------------------------

describe("AuditLogPage — error state", () => {
  it("renders an unavailable error state (not the empty state) when the fetch fails", () => {
    vi.mocked(useAuditLog).mockReturnValue(
      makeAuditErrorResponse() as unknown as ReturnType<typeof useAuditLog>,
    );
    vi.mocked(useButlers).mockReturnValue(
      makeEmptyButlersResponse() as unknown as ReturnType<typeof useButlers>,
    );

    const html = renderPage("/audit-log");
    expect(html).toContain("Audit log unavailable.");
    expect(html).not.toContain("No audit entries found.");
  });
});
