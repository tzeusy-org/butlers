import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import type { GoogleAccount } from "@/api/index.ts";
import { GoogleOAuthSection } from "@/components/settings/GoogleOAuthSection";
import { useDisconnectGoogleHealth } from "@/hooks/use-google-health";
import {
  useDisconnectAccount,
  useGoogleAccounts,
  useGoogleCredentialStatus,
  useSetPrimaryAccount,
} from "@/hooks/use-secrets";

vi.mock("@/hooks/use-google-health", () => ({
  useDisconnectGoogleHealth: vi.fn(),
}));

vi.mock("@/hooks/use-secrets", () => ({
  useGoogleAccounts: vi.fn(),
  useGoogleCredentialStatus: vi.fn(),
  useDisconnectAccount: vi.fn(),
  useSetPrimaryAccount: vi.fn(),
}));

const HEALTH_SCOPES = [
  "https://www.googleapis.com/auth/googlehealth.sleep",
  "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
  "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements",
];

const CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"];
const DRIVE_SCOPES = [
  "https://www.googleapis.com/auth/drive.readonly",
  "https://www.googleapis.com/auth/drive",
];

function account(overrides: Partial<GoogleAccount>): GoogleAccount {
  return {
    id: "acct-1",
    email: "owner@example.com",
    display_name: "Owner",
    is_primary: true,
    status: "active",
    granted_scopes: [],
    connected_at: "2026-04-01T00:00:00Z",
    last_token_refresh_at: null,
    ...overrides,
  };
}

function stubMutations() {
  const mutation = {
    mutateAsync: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  };
  vi.mocked(useDisconnectGoogleHealth).mockReturnValue(
    mutation as unknown as ReturnType<typeof useDisconnectGoogleHealth>,
  );
  vi.mocked(useDisconnectAccount).mockReturnValue(
    mutation as unknown as ReturnType<typeof useDisconnectAccount>,
  );
  vi.mocked(useSetPrimaryAccount).mockReturnValue(
    mutation as unknown as ReturnType<typeof useSetPrimaryAccount>,
  );
}

function stubCredentialStatus() {
  vi.mocked(useGoogleCredentialStatus).mockReturnValue({
    data: {
      client_id_configured: true,
      client_secret_configured: true,
      refresh_token_present: true,
      scope: null,
      oauth_health: "connected",
      oauth_health_remediation: null,
      oauth_health_detail: null,
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useGoogleCredentialStatus>);
}

function stubAccounts(accts: GoogleAccount[]) {
  vi.mocked(useGoogleAccounts).mockReturnValue({
    data: accts,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useGoogleAccounts>);
}

describe("GoogleOAuthSection scope-set picker", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    stubMutations();
    stubCredentialStatus();
  });

  it("does not render the legacy read-only granted_scopes CSV", () => {
    stubAccounts([
      account({
        granted_scopes: [...CALENDAR_SCOPES, ...DRIVE_SCOPES, ...HEALTH_SCOPES],
      }),
    ]);
    const html = renderToStaticMarkup(<GoogleOAuthSection />);
    // Legacy line used the string "Scopes: <csv>" — it MUST be gone.
    expect(html).not.toContain("Scopes:");
  });

  it("renders one row per registered scope set", () => {
    stubAccounts([account({ granted_scopes: CALENDAR_SCOPES })]);
    const html = renderToStaticMarkup(<GoogleOAuthSection />);
    expect(html).toContain("Calendar");
    expect(html).toContain("Drive");
    expect(html).toContain("Google Health");
  });

  it("shows Granted badge for scope sets whose scopes are all present", () => {
    stubAccounts([
      account({
        granted_scopes: [...CALENDAR_SCOPES, ...DRIVE_SCOPES, ...HEALTH_SCOPES],
      }),
    ]);
    const html = renderToStaticMarkup(<GoogleOAuthSection />);
    // Three granted badges expected (one per scope set).
    const matches = html.match(/>Granted</g) ?? [];
    expect(matches.length).toBe(3);
  });

  it("links the ungranted Google Health row to the scope_set=health consent URL", () => {
    stubAccounts([account({ granted_scopes: CALENDAR_SCOPES })]);
    const html = renderToStaticMarkup(<GoogleOAuthSection />);
    // scope_set=health ensures Google issues a consent covering the three
    // RESTRICTED scopes; force_consent=true forces a new refresh-token mint.
    expect(html).toContain("scope_set=health");
    expect(html).toContain("force_consent=true");
  });

  it("links account reauthorization to a consent URL that includes Google Health", () => {
    stubAccounts([account({ granted_scopes: [...CALENDAR_SCOPES, ...DRIVE_SCOPES] })]);
    const html = renderToStaticMarkup(<GoogleOAuthSection />);
    expect(html).toContain("scope_set=calendar%2Cdrive%2Chealth");
    expect(html).toContain("Re-authorize");
  });

  it("shows the Google Health connect hint when health scopes absent", () => {
    stubAccounts([account({ granted_scopes: CALENDAR_SCOPES })]);
    const html = renderToStaticMarkup(<GoogleOAuthSection />);
    expect(html).toContain(
      "Connect Google Health to enable sleep, HR, HRV, and activity",
    );
  });

  it("renders 'via full disconnect' hint for granted Calendar/Drive rows", () => {
    stubAccounts([
      account({
        granted_scopes: [...CALENDAR_SCOPES, ...DRIVE_SCOPES],
      }),
    ]);
    const html = renderToStaticMarkup(<GoogleOAuthSection />);
    // Should surface twice — once for each of the two granted non-selective sets.
    const matches = html.match(/via full disconnect/g) ?? [];
    expect(matches.length).toBe(2);
  });

  it("renders selective disconnect button for granted Google Health", () => {
    stubAccounts([
      account({
        granted_scopes: HEALTH_SCOPES,
      }),
    ]);
    const html = renderToStaticMarkup(<GoogleOAuthSection />);
    expect(html).toContain("scope-set-revoke-health");
  });
});
