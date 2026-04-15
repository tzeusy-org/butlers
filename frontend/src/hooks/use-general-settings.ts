import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getGeneralSettings, updateGeneralSettings } from "@/api/index.ts";
import type { GeneralSettingsUpdate } from "@/api/index.ts";

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

export function useUpdateGeneralSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: GeneralSettingsUpdate) => updateGeneralSettings(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: generalSettingsKeys.settings() });
    },
  });
}
