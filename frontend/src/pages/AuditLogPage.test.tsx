/**
 * AuditLogPage — unit tests.
 *
 * Covers:
 * - ?key= and ?actor= deep-link wiring (bu-zpivp) — preserved
 * - New-schema filter params: actor (filter bar), action, since (bu-ffnyz)
 * - Table renders new-schema AuditLogEntry rows
 */

import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import AuditLogPage from "@/pages/AuditLogPage";
import type { AuditLogParams, AuditLogEntry } from "@/api/types";

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
    isError: false,
  };
}

function makeAuditErrorResponse() {
  return {
    data: undefined,
    isLoading: false,
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
