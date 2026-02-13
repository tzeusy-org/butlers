import { useState } from "react";

import { AutoRefreshToggle } from "@/components/ui/auto-refresh-toggle";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAutoRefresh } from "@/hooks/use-auto-refresh";
import { useDarkMode } from "@/hooks/useDarkMode";
import { RECENT_SEARCHES_KEY } from "@/lib/local-settings";

type ThemeOption = "light" | "dark" | "system";

function getRecentSearchCount() {
  try {
    const raw = localStorage.getItem(RECENT_SEARCHES_KEY);
    if (!raw) return 0;
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.length : 0;
  } catch {
    return 0;
  }
}

export default function SettingsPage() {
  const { theme, resolvedTheme, setTheme } = useDarkMode();
  const autoRefreshControl = useAutoRefresh(10_000);

  const [recentSearchCount, setRecentSearchCount] = useState(getRecentSearchCount);

  function handleThemeChange(value: string) {
    setTheme(value as ThemeOption);
  }

  function clearRecentSearches() {
    try {
      localStorage.removeItem(RECENT_SEARCHES_KEY);
      setRecentSearchCount(0);
    } catch {
      // Ignore localStorage write failures.
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
        <p className="text-muted-foreground mt-1">
          Local dashboard preferences for this browser.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Appearance</CardTitle>
          <CardDescription>Set the UI theme preference.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="max-w-xs space-y-1">
            <label className="text-muted-foreground text-xs font-medium">
              Theme
            </label>
            <Select value={theme} onValueChange={handleThemeChange}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="system">System</SelectItem>
                <SelectItem value="light">Light</SelectItem>
                <SelectItem value="dark">Dark</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <p className="text-muted-foreground text-sm">
            Active theme: <span className="font-medium capitalize">{resolvedTheme}</span>
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Live Refresh Defaults</CardTitle>
          <CardDescription>
            Default behavior used by pages with live auto-refresh controls.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <AutoRefreshToggle
            enabled={autoRefreshControl.enabled}
            interval={autoRefreshControl.interval}
            onToggle={autoRefreshControl.setEnabled}
            onIntervalChange={autoRefreshControl.setInterval}
          />
          <p className="text-muted-foreground text-sm">
            This currently applies to Sessions and Timeline.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Command Palette</CardTitle>
          <CardDescription>Manage local quick-search history.</CardDescription>
        </CardHeader>
        <CardContent className="flex items-center justify-between gap-4">
          <p className="text-muted-foreground text-sm">
            Saved recent searches: <span className="font-medium">{recentSearchCount}</span>
          </p>
          <Button
            variant="outline"
            size="sm"
            disabled={recentSearchCount === 0}
            onClick={clearRecentSearches}
          >
            Clear recent searches
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
