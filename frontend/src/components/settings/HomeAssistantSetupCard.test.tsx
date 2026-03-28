import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { HomeAssistantSetupCard, HomeAssistantSection } from "@/components/settings/HomeAssistantSetupCard";
import {
  useHomeAssistantStatus,
  useConfigureHomeAssistant,
  useDeleteHomeAssistantConfig,
} from "@/hooks/use-home-assistant";

vi.mock("@/hooks/use-home-assistant", () => ({
  useHomeAssistantStatus: vi.fn(),
  useConfigureHomeAssistant: vi.fn(),
  useDeleteHomeAssistantConfig: vi.fn(),
}));

type UseQueryResult = ReturnType<typeof useHomeAssistantStatus>;
type UseMutationResult = {
  mutateAsync: ReturnType<typeof vi.fn>;
  isPending: boolean;
  isError: boolean;
  error: Error | null;
};

function mockStatus(state: Partial<UseQueryResult>) {
  vi.mocked(useHomeAssistantStatus).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...state,
  } as UseQueryResult);
}

function mockMutations(opts: { isPending?: boolean; isError?: boolean; error?: Error | null } = {}) {
  const mutation: UseMutationResult = {
    mutateAsync: vi.fn(),
    isPending: opts.isPending ?? false,
    isError: opts.isError ?? false,
    error: opts.error ?? null,
  };
  vi.mocked(useConfigureHomeAssistant).mockReturnValue(mutation as unknown as ReturnType<typeof useConfigureHomeAssistant>);
  vi.mocked(useDeleteHomeAssistantConfig).mockReturnValue(mutation as unknown as ReturnType<typeof useDeleteHomeAssistantConfig>);
}

describe("HomeAssistantSetupCard", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockMutations();
  });

  it("renders loading skeleton", () => {
    mockStatus({ isLoading: true });
    const html = renderToStaticMarkup(<HomeAssistantSetupCard />);
    expect(html).toContain("Home Assistant");
  });

  it("renders error state", () => {
    mockStatus({ isError: true, error: new Error("timeout") });
    const html = renderToStaticMarkup(<HomeAssistantSetupCard />);
    expect(html).toContain("Failed to fetch Home Assistant status");
    expect(html).toContain("Error");
  });

  it("renders not_configured state with config form", () => {
    mockStatus({
      data: {
        state: "not_configured",
        url_configured: false,
        token_configured: false,
        masked_url: null,
      },
    });
    const html = renderToStaticMarkup(<HomeAssistantSetupCard />);
    expect(html).toContain("Not configured");
    expect(html).toContain("Home Assistant URL");
    expect(html).toContain("Long-lived access token");
  });

  it("renders connected state with masked URL", () => {
    mockStatus({
      data: {
        state: "connected",
        url_configured: true,
        token_configured: true,
        masked_url: "http://homeassistant.local:8123",
      },
    });
    const html = renderToStaticMarkup(<HomeAssistantSetupCard />);
    expect(html).toContain("Connected");
    expect(html).toContain("http://homeassistant.local:8123");
    expect(html).toContain("Re-configure");
    expect(html).toContain("Disconnect");
  });

  it("does not render token input in connected state (no credential leak)", () => {
    mockStatus({
      data: {
        state: "connected",
        url_configured: true,
        token_configured: true,
        masked_url: "http://homeassistant.local:8123",
      },
    });
    const html = renderToStaticMarkup(<HomeAssistantSetupCard />);
    // Connected state should NOT render the token input field
    expect(html).not.toContain("ha-token");
    expect(html).not.toContain("Long-lived access token");
    // Masked URL is shown but no password input
    expect(html).toContain("http://homeassistant.local:8123");
  });

  it("renders disconnected state with form", () => {
    mockStatus({
      data: {
        state: "disconnected",
        url_configured: true,
        token_configured: false,
        masked_url: null,
      },
    });
    const html = renderToStaticMarkup(<HomeAssistantSetupCard />);
    expect(html).toContain("Disconnected");
    expect(html).toContain("Home Assistant URL");
  });
});

describe("HomeAssistantSection (embeddable)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockMutations();
  });

  it("renders loading state", () => {
    mockStatus({ isLoading: true });
    const html = renderToStaticMarkup(<HomeAssistantSection />);
    expect(html).toContain("Home Assistant");
  });

  it("renders not_configured state", () => {
    mockStatus({
      data: {
        state: "not_configured",
        url_configured: false,
        token_configured: false,
        masked_url: null,
      },
    });
    const html = renderToStaticMarkup(<HomeAssistantSection />);
    expect(html).toContain("Not configured");
    expect(html).toContain("Home Assistant URL");
  });

  it("renders connected state", () => {
    mockStatus({
      data: {
        state: "connected",
        url_configured: true,
        token_configured: true,
        masked_url: "http://ha.home:8123",
      },
    });
    const html = renderToStaticMarkup(<HomeAssistantSection />);
    expect(html).toContain("Connected");
    expect(html).toContain("http://ha.home:8123");
  });
});
