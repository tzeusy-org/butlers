import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { GeneralSettingsCard } from "@/components/GeneralSettingsCard";
import {
  useGeneralSettings,
  useUpdateGeneralSettings,
} from "@/hooks/use-general-settings";

vi.mock("@/hooks/use-general-settings", () => ({
  useGeneralSettings: vi.fn(),
  useUpdateGeneralSettings: vi.fn(),
}));

describe("GeneralSettingsCard", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useUpdateGeneralSettings).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateGeneralSettings>);
  });

  it("renders loading state", () => {
    vi.mocked(useGeneralSettings).mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useGeneralSettings>);

    const html = renderToStaticMarkup(<GeneralSettingsCard />);

    expect(html).toContain("General");
  });

  it("renders current general prompt defaults", () => {
    vi.mocked(useGeneralSettings).mockReturnValue({
      data: {
        data: {
          timezone: "Asia/Singapore",
          timezone_label: "Asia/Singapore (GMT+08:00)",
          language: "en-US",
          date_format: "YYYY-mm-dd",
          time_format: "HH:MM",
          week_starts_on: "Monday",
          currency: "USD",
          measurement_system: "metric",
        },
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useGeneralSettings>);

    const html = renderToStaticMarkup(<GeneralSettingsCard />);

    expect(html).toContain("Default timezone");
    expect(html).toContain("Language");
    expect(html).toContain("Date format");
    expect(html).toContain("Time format");
    expect(html).toContain("Week starts on");
    expect(html).toContain("Currency");
    expect(html).toContain("Asia/Singapore (GMT+08:00)");
    expect(html).toContain("Unless otherwise stated, assume times and timezones are in");
    expect(html).toContain("Default language/locale: en-US.");
    expect(html).toContain("Use metric measurements.");
  });
});
