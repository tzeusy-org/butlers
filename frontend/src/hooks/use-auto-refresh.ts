import { useEffect, useState } from "react";
import {
  AUTO_REFRESH_ENABLED_KEY,
  AUTO_REFRESH_INTERVAL_KEY,
  isAutoRefreshInterval,
  readBooleanSetting,
  readIntervalSetting,
  writeBooleanSetting,
  writeIntervalSetting,
} from "@/lib/local-settings";

export function useAutoRefresh(defaultInterval = 10_000) {
  const [enabled, setEnabled] = useState(() =>
    readBooleanSetting(AUTO_REFRESH_ENABLED_KEY, true),
  );
  const [interval, setIntervalValue] = useState(() =>
    readIntervalSetting(AUTO_REFRESH_INTERVAL_KEY, defaultInterval),
  );

  useEffect(() => {
    writeBooleanSetting(AUTO_REFRESH_ENABLED_KEY, enabled);
  }, [enabled]);

  useEffect(() => {
    writeIntervalSetting(AUTO_REFRESH_INTERVAL_KEY, interval);
  }, [interval]);

  function setInterval(value: number) {
    if (!isAutoRefreshInterval(value)) return;
    setIntervalValue(value);
  }

  return {
    refetchInterval: enabled ? interval : false as const,
    enabled,
    interval,
    setEnabled,
    setInterval,
  };
}
