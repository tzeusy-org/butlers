import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useHomeAtmosphereCurrent, useUpdateHomeAtmosphereLocation } from "@/hooks/use-home";

import { Panel } from "./atoms";

type CoordinateField = "latitude" | "longitude";

interface FieldErrors {
  latitude?: string;
  longitude?: string;
}

function validateCoordinates(latitudeValue: string, longitudeValue: string): FieldErrors {
  const latitude = Number(latitudeValue);
  const longitude = Number(longitudeValue);

  if (!latitudeValue.trim() || !Number.isFinite(latitude) || latitude < -90 || latitude > 90) {
    return { latitude: "Latitude must be between -90 and 90." };
  }

  if (
    !longitudeValue.trim() ||
    !Number.isFinite(longitude) ||
    longitude < -180 ||
    longitude > 180
  ) {
    return { longitude: "Longitude must be between -180 and 180." };
  }

  return {};
}

function mutationErrorMessage(error: unknown): string {
  const status =
    error instanceof ApiError
      ? error.status
      : typeof error === "object" && error !== null && "status" in error
        ? (error as { status?: unknown }).status
        : undefined;

  if (status === 422) {
    return "Check the coordinate values and try again.";
  }

  if (status === 503) {
    return "The owner profile is unavailable. Try again after it is restored.";
  }

  return "Check your connection and try again.";
}

function FeedHealth({
  stale,
  sourceError,
  lastError,
}: {
  stale: boolean;
  sourceError: boolean;
  lastError: string | null;
}) {
  if (stale && sourceError) {
    return (
      <p className="text-sm text-destructive" role="alert">
        The atmosphere feed is stale and its source last failed.
        {lastError ? <span className="block mt-1 text-xs">{lastError}</span> : null}
      </p>
    );
  }

  if (sourceError) {
    return (
      <p className="text-sm text-destructive" role="alert">
        The atmosphere source last failed. Saved coordinates remain available.
        {lastError ? <span className="block mt-1 text-xs">{lastError}</span> : null}
      </p>
    );
  }

  if (stale) {
    return (
      <p className="text-sm text-[var(--amber-text)]" role="status">
        The atmosphere feed is stale. Saved coordinates remain available.
      </p>
    );
  }

  return null;
}

/**
 * Owner-only location form for the Home atmosphere feed. Saving coordinates
 * intentionally does not imply a synchronous refresh; the existing scheduled
 * job owns that work.
 */
export function HomeAtmosphereLocationPanel() {
  const {
    data: current,
    isLoading,
    isError,
    error: currentError,
    refetch,
  } = useHomeAtmosphereCurrent();
  const { mutate, isPending } = useUpdateHomeAtmosphereLocation();
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [locationWasSaved, setLocationWasSaved] = useState(false);
  const hasUserEdited = useRef(false);
  const hydratedLocation = useRef<string | null>(null);

  useEffect(() => {
    if (!current?.configured || current.latitude === null || current.longitude === null) {
      return;
    }

    const locationKey = `${current.latitude},${current.longitude}`;
    if (hasUserEdited.current || hydratedLocation.current === locationKey) {
      return;
    }

    setLatitude(String(current.latitude));
    setLongitude(String(current.longitude));
    hydratedLocation.current = locationKey;
  }, [current]);

  const onCoordinateChange = (field: CoordinateField, value: string) => {
    hasUserEdited.current = true;
    setSaveError(null);
    setSaveSuccess(false);
    setFieldErrors((errors) => ({ ...errors, [field]: undefined }));

    if (field === "latitude") {
      setLatitude(value);
    } else {
      setLongitude(value);
    }
  };

  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const errors = validateCoordinates(latitude, longitude);
    setFieldErrors(errors);
    setSaveError(null);
    setSaveSuccess(false);

    if (Object.keys(errors).length > 0) {
      return;
    }

    mutate(
      { latitude: Number(latitude), longitude: Number(longitude) },
      {
        onSuccess: () => {
          setLocationWasSaved(true);
          setSaveSuccess(true);
        },
        onError: (error) => {
          setSaveError(mutationErrorMessage(error));
        },
      },
    );
  };

  const showingPending = isPending && !saveSuccess && !saveError;
  const locationState = current?.configured
    ? "Home location is configured."
    : locationWasSaved
      ? "Home location is saved. Feed status will update after the next scheduled refresh."
      : "No home location is configured yet.";

  if (isLoading && !current) {
    return (
      <Panel title="Atmosphere location" span={4} testId="atmosphere-location-panel">
        <p className="text-sm text-muted-foreground" role="status" aria-live="polite">
          Loading saved home location...
        </p>
      </Panel>
    );
  }

  if (isError && !current) {
    return (
      <Panel title="Atmosphere location" span={4} testId="atmosphere-location-panel">
        <div className="space-y-3">
          <p className="text-sm text-destructive" role="alert">
            Couldn&apos;t load the saved home location.
            {currentError instanceof Error && currentError.message ? (
              <span className="block mt-1 text-xs">{currentError.message}</span>
            ) : null}
          </p>
          <Button type="button" variant="outline" size="sm" onClick={() => void refetch()}>
            Retry
          </Button>
        </div>
      </Panel>
    );
  }

  return (
    <Panel title="Atmosphere location" span={4} testId="atmosphere-location-panel">
      <div className="space-y-4">
        <div className="space-y-1">
          <p className="text-sm text-muted-foreground">
            {locationState}
          </p>
          {current ? (
            <FeedHealth
              stale={current.stale}
              sourceError={current.source_error}
              lastError={current.last_error}
            />
          ) : null}
        </div>

        {isError ? (
          <div className="flex items-center gap-3">
            <p className="text-sm text-destructive" role="alert">
              The saved location may be out of date.
            </p>
            <Button type="button" variant="outline" size="sm" onClick={() => void refetch()}>
              Retry
            </Button>
          </div>
        ) : null}

        <form className="space-y-4" noValidate onSubmit={onSubmit}>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="home-atmosphere-latitude">
                Latitude
              </label>
              <Input
                id="home-atmosphere-latitude"
                type="number"
                inputMode="decimal"
                min={-90}
                max={90}
                step="any"
                required
                value={latitude}
                aria-invalid={Boolean(fieldErrors.latitude)}
                aria-describedby={fieldErrors.latitude ? "home-atmosphere-latitude-error" : undefined}
                onChange={(event) => onCoordinateChange("latitude", event.target.value)}
              />
              {fieldErrors.latitude ? (
                <p className="text-xs text-destructive" id="home-atmosphere-latitude-error" role="alert">
                  {fieldErrors.latitude}
                </p>
              ) : null}
            </div>

            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="home-atmosphere-longitude">
                Longitude
              </label>
              <Input
                id="home-atmosphere-longitude"
                type="number"
                inputMode="decimal"
                min={-180}
                max={180}
                step="any"
                required
                value={longitude}
                aria-invalid={Boolean(fieldErrors.longitude)}
                aria-describedby={fieldErrors.longitude ? "home-atmosphere-longitude-error" : undefined}
                onChange={(event) => onCoordinateChange("longitude", event.target.value)}
              />
              {fieldErrors.longitude ? (
                <p className="text-xs text-destructive" id="home-atmosphere-longitude-error" role="alert">
                  {fieldErrors.longitude}
                </p>
              ) : null}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={showingPending}>
              {showingPending ? "Saving home location..." : "Save home location"}
            </Button>
            {showingPending ? (
              <p className="text-sm text-muted-foreground" role="status" aria-live="polite">
                Saving home location...
              </p>
            ) : null}
            {saveSuccess ? (
              <p className="text-sm text-[var(--green)]" role="status" aria-live="polite">
                Home location saved. The next scheduled refresh will pick up this change.
              </p>
            ) : null}
            {saveError ? (
              <p className="text-sm text-destructive" role="alert">
                {saveError}
              </p>
            ) : null}
          </div>
        </form>
      </div>
    </Panel>
  );
}
