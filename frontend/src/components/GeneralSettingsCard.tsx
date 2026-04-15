import { useEffect, useState } from "react";

import { Clock3, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useGeneralSettings, useUpdateGeneralSettings } from "@/hooks/use-general-settings";

type SupportedIntl = typeof Intl & {
  supportedValuesOf?: (key: string) => string[];
};

interface TimezoneOption {
  value: string;
  label: string;
  offsetMinutes: number;
}

const DATE_FORMAT_OPTIONS = ["YYYY-mm-dd", "MM/dd/YYYY", "dd/MM/YYYY"] as const;
const TIME_FORMAT_OPTIONS = ["HH:MM", "hh:mm A"] as const;
const WEEK_START_OPTIONS = ["Monday", "Sunday"] as const;

interface GeneralSettingsFormState {
  timezone: string;
  language: string;
  date_format: string;
  time_format: string;
  week_starts_on: string;
  currency: string;
}

function parseOffsetMinutes(label: string): number {
  const match = /GMT(?:(?<sign>[+-])(?<hours>\d{1,2})(?::(?<minutes>\d{2}))?)?$/.exec(label);
  if (!match?.groups?.sign || !match.groups.hours) {
    return 0;
  }
  const sign = match.groups.sign === "-" ? -1 : 1;
  const hours = Number(match.groups.hours);
  const minutes = Number(match.groups.minutes ?? "0");
  return sign * (hours * 60 + minutes);
}

function timezoneOffsetLabel(timezone: string): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: timezone,
      timeZoneName: "longOffset",
      year: "numeric",
    }).formatToParts(new Date());
    const offset = parts.find((part) => part.type === "timeZoneName")?.value ?? "GMT+00:00";
    return offset === "GMT" ? "GMT+00:00" : offset;
  } catch {
    return "GMT+00:00";
  }
}

function buildTimezoneOptions(): TimezoneOption[] {
  const intl = Intl as SupportedIntl;
  const supported = typeof intl.supportedValuesOf === "function"
    ? intl.supportedValuesOf("timeZone")
    : [Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"];
  const values = Array.from(new Set(["UTC", ...supported]));
  return values
    .map((value) => {
      const offset = timezoneOffsetLabel(value);
      return {
        value,
        label: `${value} (${offset})`,
        offsetMinutes: parseOffsetMinutes(offset),
      };
    })
    .sort((a, b) => a.offsetMinutes - b.offsetMinutes || a.value.localeCompare(b.value));
}

const TIMEZONE_OPTIONS = buildTimezoneOptions();

function labelForTimezone(timezone: string, fallback?: string): string {
  return TIMEZONE_OPTIONS.find((option) => option.value === timezone)?.label ?? fallback ?? timezone;
}

export function GeneralSettingsCard() {
  const settingsQuery = useGeneralSettings();
  const updateMutation = useUpdateGeneralSettings();
  const settings = settingsQuery.data?.data;

  const [formState, setFormState] = useState<GeneralSettingsFormState>({
    timezone: "",
    language: "",
    date_format: "",
    time_format: "",
    week_starts_on: "",
    currency: "",
  });

  useEffect(() => {
    if (settings) {
      setFormState({
        timezone: settings.timezone,
        language: settings.language,
        date_format: settings.date_format,
        time_format: settings.time_format,
        week_starts_on: settings.week_starts_on,
        currency: settings.currency,
      });
    }
  }, [settings]);

  const currentTimezone = settings?.timezone ?? "UTC";
  const currentLabel = settings?.timezone_label ?? labelForTimezone(currentTimezone, "UTC (GMT+00:00)");
  const effectiveSelectedTimezone = formState.timezone || currentTimezone;
  const selectedLabel = labelForTimezone(effectiveSelectedTimezone, currentLabel);
  const effectiveLanguage = formState.language || settings?.language || "en-US";
  const effectiveDateFormat = formState.date_format || settings?.date_format || "YYYY-mm-dd";
  const effectiveTimeFormat = formState.time_format || settings?.time_format || "HH:MM";
  const effectiveWeekStartsOn = formState.week_starts_on || settings?.week_starts_on || "Monday";
  const effectiveCurrency = (formState.currency || settings?.currency || "USD").toUpperCase();
  const effectiveMeasurementSystem = settings?.measurement_system || "metric";
  const isDirty = settings != null && (
    effectiveSelectedTimezone !== settings.timezone ||
    effectiveLanguage !== settings.language ||
    effectiveDateFormat !== settings.date_format ||
    effectiveTimeFormat !== settings.time_format ||
    effectiveWeekStartsOn !== settings.week_starts_on ||
    effectiveCurrency !== settings.currency
  );

  async function handleSave() {
    try {
      await updateMutation.mutateAsync({
        timezone: effectiveSelectedTimezone,
        language: effectiveLanguage,
        date_format: effectiveDateFormat,
        time_format: effectiveTimeFormat,
        week_starts_on: effectiveWeekStartsOn,
        currency: effectiveCurrency,
      });
      toast.success("General settings updated");
    } catch (error) {
      toast.error(
        `Failed to update settings: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  }

  if (settingsQuery.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Clock3 className="h-5 w-5" />
            General
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-28 w-full" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Clock3 className="h-5 w-5" />
          General
        </CardTitle>
        <CardDescription>
          Shared defaults injected into every butler prompt.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="general-timezone">Default timezone</Label>
          <Select
            value={effectiveSelectedTimezone}
            onValueChange={(value) => setFormState((current) => ({ ...current, timezone: value }))}
          >
            <SelectTrigger id="general-timezone">
              <SelectValue placeholder="Select timezone" />
            </SelectTrigger>
            <SelectContent className="max-h-80">
              {TIMEZONE_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            Offsets reflect the current instant, so DST-aware zones may shift between GMT values.
          </p>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="general-language">Language</Label>
            <Input
              id="general-language"
              value={effectiveLanguage}
              onChange={(event) =>
                setFormState((current) => ({ ...current, language: event.target.value }))
              }
              placeholder="en-US"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="general-currency">Currency</Label>
            <Input
              id="general-currency"
              value={effectiveCurrency}
              onChange={(event) =>
                setFormState((current) => ({ ...current, currency: event.target.value.toUpperCase() }))
              }
              placeholder="USD"
              maxLength={3}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="general-date-format">Date format</Label>
            <Select
              value={effectiveDateFormat}
              onValueChange={(value) => setFormState((current) => ({ ...current, date_format: value }))}
            >
              <SelectTrigger id="general-date-format">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DATE_FORMAT_OPTIONS.map((option) => (
                  <SelectItem key={option} value={option}>
                    {option}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="general-time-format">Time format</Label>
            <Select
              value={effectiveTimeFormat}
              onValueChange={(value) => setFormState((current) => ({ ...current, time_format: value }))}
            >
              <SelectTrigger id="general-time-format">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TIME_FORMAT_OPTIONS.map((option) => (
                  <SelectItem key={option} value={option}>
                    {option}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="general-week-start">Week starts on</Label>
            <Select
              value={effectiveWeekStartsOn}
              onValueChange={(value) =>
                setFormState((current) => ({ ...current, week_starts_on: value }))
              }
            >
              <SelectTrigger id="general-week-start">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {WEEK_START_OPTIONS.map((option) => (
                  <SelectItem key={option} value={option}>
                    {option}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="general-measurement-system">Measurement system</Label>
            <Input
              id="general-measurement-system"
              value={effectiveMeasurementSystem}
              readOnly
              disabled
            />
          </div>
        </div>

        <div className="rounded-lg border bg-muted/30 p-3 text-sm">
          <p className="font-medium">Current prompt assumption</p>
          <div className="mt-1 space-y-1 text-muted-foreground">
            <p>Unless otherwise stated, assume times and timezones are in {selectedLabel}.</p>
            <p>Default language/locale: {effectiveLanguage}.</p>
            <p>Default date format: {effectiveDateFormat}.</p>
            <p>Default time format: {effectiveTimeFormat}.</p>
            <p>Week starts on: {effectiveWeekStartsOn}.</p>
            <p>Default currency: {effectiveCurrency}.</p>
            <p>Use metric measurements.</p>
          </div>
        </div>

        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-muted-foreground">
            Active timezone: <span className="font-medium text-foreground">{currentLabel}</span>
          </p>
          <Button onClick={handleSave} disabled={!isDirty || updateMutation.isPending}>
            {updateMutation.isPending ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Saving...
              </>
            ) : (
              "Save"
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
