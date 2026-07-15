import { useQuery } from "@tanstack/react-query";

import { getGeneralSettings } from "@/api/index.ts";

export const generalSettingsKeys = {
  settings: () => ["general-settings"] as const,
} as const;

export function useGeneralSettings() {
  return useQuery({
    queryKey: generalSettingsKeys.settings(),
    queryFn: getGeneralSettings,
    staleTime: 60_000,
    retry: false,
  });
}
