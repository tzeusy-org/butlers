/**
 * AuditLogPage — unit tests for ?key= and ?actor= deep-link wiring (bu-zpivp).
 *
 * Verifies:
 * - ?key= from URL is forwarded to getAuditLog via useAuditLog
 * - ?actor= from URL is forwarded to getAuditLog via useAuditLog
 * - Key filter chip renders when ?key= is present
 * - Actor filter chip renders when ?actor= is present
 * - No filter chips when neither ?key= nor ?actor= is present
 * - Existing filters (butler, operation, since, until) still build params
 */

import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import AuditLogPage from "@/pages/AuditLogPage";
import type { AuditLogParams } from "@/api/types";

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

function makeEmptyAuditResponse() {
  return {
    data: { data: [], meta: { total: 0, offset: 0, limit: 20 } },
    isLoading: false,
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

function setupDefaults() {
  vi.mocked(useAuditLog).mockReturnValue(makeEmptyAuditResponse() as unknown as ReturnType<typeof useAuditLog>);
  vi.mocked(useButlers).mockReturnValue(makeEmptyButlersResponse() as unknown as ReturnType<typeof useButlers>);
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
// Existing URL filters still work (butler, operation, since, until)
// ---------------------------------------------------------------------------

describe("AuditLogPage — existing URL filter params", () => {
  it("reads butler filter from URL and builds params", () => {
    setupDefaults();
    renderPage("/audit-log?butler=general");

    const calls = vi.mocked(useAuditLog).mock.calls;
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.butler).toBe("general");
  });

  it("excludes butler from params when value is 'all'", () => {
    setupDefaults();
    renderPage("/audit-log");

    const calls = vi.mocked(useAuditLog).mock.calls;
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.butler).toBeUndefined();
  });

  it("reads since filter from URL and builds params", () => {
    setupDefaults();
    renderPage("/audit-log?since=2026-01-01");

    const calls = vi.mocked(useAuditLog).mock.calls;
    const params: AuditLogParams = calls[calls.length - 1][0] ?? {};
    expect(params.since).toBe("2026-01-01");
  });
});
