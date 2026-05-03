/**
 * Tests for SystemPage.
 *
 * All tests use renderToStaticMarkup with mocked hooks to keep execution fast
 * and avoid network calls.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import SystemPage from "@/pages/SystemPage";
import {
  useBackupFacts,
  useButlerHeartbeats,
  useDatabaseFacts,
  useEgressFacts,
  useInstanceFacts,
} from "@/hooks/use-system";
import { ApiError } from "@/api/index";

// ---------------------------------------------------------------------------
// Mock all hooks used by SystemPage
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-system", () => ({
  useInstanceFacts: vi.fn(),
  useDatabaseFacts: vi.fn(),
  useBackupFacts: vi.fn(),
  useEgressFacts: vi.fn(),
  useButlerHeartbeats: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Default hook stubs (all loading)
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

function setAllLoading() {
  vi.mocked(useInstanceFacts).mockReturnValue({
    data: undefined,
    isLoading: true,
    error: null,
  } as AnyMock);

  vi.mocked(useDatabaseFacts).mockReturnValue({
    data: undefined,
    isLoading: true,
    error: null,
  } as AnyMock);

  vi.mocked(useBackupFacts).mockReturnValue({
    data: undefined,
    isLoading: true,
    error: null,
  } as AnyMock);

  vi.mocked(useEgressFacts).mockReturnValue({
    data: undefined,
    isLoading: true,
    error: null,
    isForbidden: false,
  } as AnyMock);

  vi.mocked(useButlerHeartbeats).mockReturnValue({
    data: undefined,
    isLoading: true,
    error: null,
  } as AnyMock);
}

function setAllSuccess() {
  vi.mocked(useInstanceFacts).mockReturnValue({
    data: { data: { version: "1.0.0", uptime_seconds: 3600, started_at: "2026-01-01T00:00:00Z" }, meta: {} },
    isLoading: false,
    error: null,
  } as AnyMock);

  vi.mocked(useDatabaseFacts).mockReturnValue({
    data: { data: { total_size_bytes: 1024, schemas: [], largest_tables: [], growth_rate_bytes_per_day: null }, meta: {} },
    isLoading: false,
    error: null,
  } as AnyMock);

  vi.mocked(useBackupFacts).mockReturnValue({
    data: { data: { last_backup_at: null, last_backup_size_bytes: null, backup_source_reachable: true, backup_history: [] }, meta: {} },
    isLoading: false,
    error: null,
  } as AnyMock);

  vi.mocked(useEgressFacts).mockReturnValue({
    data: { data: { actors: [{ actor_id: "anthropic.claude", display_name: "Anthropic Claude API", last_seen_at: "2026-01-01T00:00:00Z", total_calls: 5, data_types: ["session_prompt"] }], catalog_covers_from: null }, meta: {} },
    isLoading: false,
    error: null,
    isForbidden: false,
  } as AnyMock);

  vi.mocked(useButlerHeartbeats).mockReturnValue({
    data: { data: { butlers: [{ name: "general", last_heartbeat_at: "2026-01-01T00:00:00Z", last_session_at: null, active_session_count: 0, heartbeat_age_seconds: 120 }] }, meta: {} },
    isLoading: false,
    error: null,
  } as AnyMock);
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SystemPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("SystemPage -- page title and description", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setAllLoading();
  });

  it("renders the page title 'System'", () => {
    const html = renderPage();
    expect(html).toContain("System");
  });

  it("renders the page description", () => {
    const html = renderPage();
    expect(html).toContain("Your instance, your data, your butlers.");
  });
});

describe("SystemPage -- breadcrumbs", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setAllLoading();
  });

  it("does not render an inline Breadcrumb nav (PageHeader handles navigation)", () => {
    const html = renderPage();
    // SystemPage does not pass breadcrumbs to <Page> — the shell's PageHeader
    // auto-builds the breadcrumb row from the route path, so there must be no
    // inline aria-label="Breadcrumb" nav inside the page content.
    const occurrences = (html.match(/aria-label="Breadcrumb"/g) ?? []).length;
    expect(occurrences).toBe(0);
  });

  it("renders no inline breadcrumb links (shell header carries navigation)", () => {
    const html = renderPage();
    // No aria-label="Breadcrumb" nav in the page body; shell PageHeader owns that.
    expect(html).not.toContain('aria-label="Breadcrumb"');
  });

  it("does not render a Home link inside the page body", () => {
    const html = renderPage();
    // Without an explicit breadcrumbs prop, the Page primitive renders no crumb links.
    expect(html).not.toMatch(/href="\/"\s*[^>]*>Home/);
  });
});

describe("SystemPage -- tiles render with mock data", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setAllSuccess();
  });

  it("renders Version tile with version data", () => {
    const html = renderPage();
    expect(html).toContain("Version");
    expect(html).toContain("1.0.0");
  });

  it("renders Database Size tile with humanized size data", () => {
    const html = renderPage();
    expect(html).toContain("Database Size");
    expect(html).toContain("1.0 KB");
  });

  it("renders Backups tile", () => {
    const html = renderPage();
    expect(html).toContain("Backups");
  });

  it("renders Data Egress tile with actor data", () => {
    const html = renderPage();
    expect(html).toContain("Data Egress");
    expect(html).toContain("Anthropic Claude API");
  });

  it("renders Butler Heartbeats tile with butler data", () => {
    const html = renderPage();
    expect(html).toContain("Butler Heartbeats");
    expect(html).toContain("general");
  });
});

describe("SystemPage -- egress 403 handling", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setAllSuccess();
  });

  it("renders 'Owner only' indicator when egress returns 403", () => {
    const forbidden403 = new ApiError("forbidden", "Owner contact not found", 403);
    vi.mocked(useEgressFacts).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: forbidden403,
      isForbidden: true,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("Owner only");
    // Page must not crash -- other tiles still render
    expect(html).toContain("Version");
    expect(html).toContain("Butler Heartbeats");
  });

  it("does not crash or show generic error for 403 on egress", () => {
    const forbidden403 = new ApiError("forbidden", "Owner contact not found", 403);
    vi.mocked(useEgressFacts).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: forbidden403,
      isForbidden: true,
    } as AnyMock);

    const html = renderPage();
    // The generic "Failed to load" text should NOT appear for a 403
    const egressSection = html.slice(html.indexOf("Data Egress"));
    const nextTileIdx = egressSection.indexOf("Butler Heartbeats");
    const egressContent = egressSection.slice(0, nextTileIdx);
    expect(egressContent).not.toContain("Failed to load");
  });
});

describe("SystemPage -- backup source unreachable", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setAllSuccess();
  });

  it("renders 'Backup status unavailable' when backup_source_reachable is false", () => {
    vi.mocked(useBackupFacts).mockReturnValue({
      data: { data: { last_backup_at: null, last_backup_size_bytes: null, backup_source_reachable: false, backup_history: [] }, meta: {} },
      isLoading: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("Backup status unavailable");
  });
});
