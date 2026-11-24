import { useState } from "react";

import { ApiError } from "@/api/client.ts";
import type { OwnerPlace, OwnerPlaceInput, OwnerPlaceKind } from "@/api/home-places.ts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useDeleteOwnerPlace, useOwnerPlaces, useSaveOwnerPlace } from "@/hooks/use-home-places.ts";

import { Panel } from "@/components/ui/atoms";

const KIND_CHOICES: ReadonlyArray<{ value: OwnerPlaceKind; name: string }> = [
  { value: "home", name: "Home" },
  { value: "work", name: "Work" },
  { value: "airport", name: "Airport" },
  { value: "other", name: "Other" },
];

const ZONE_PATTERN = /^zone\.[a-z0-9_]+$/;
const ENCODER = new TextEncoder();

interface Draft {
  label: string;
  kind: OwnerPlaceKind;
  lat: string;
  lon: string;
  radius: string;
  aliases: string;
  ssids: string;
  zone: string;
  paused: boolean;
}

const EMPTY_DRAFT: Draft = {
  label: "",
  kind: "other",
  lat: "",
  lon: "",
  radius: "150",
  aliases: "",
  ssids: "",
  zone: "",
  paused: false,
};

function draftFrom(place: OwnerPlace): Draft {
  return {
    label: place.label,
    kind: place.kind,
    lat: String(place.lat),
    lon: String(place.lon),
    radius: String(place.radius_m),
    aliases: place.aliases.join("\n"),
    ssids: place.ssids.join("\n"),
    zone: place.ha_zone ?? "",
    paused: place.paused,
  };
}

function inputFrom(place: OwnerPlace, overrides: Partial<OwnerPlaceInput>): OwnerPlaceInput {
  return {
    label: place.label,
    kind: place.kind,
    lat: place.lat,
    lon: place.lon,
    radius_m: place.radius_m,
    aliases: place.aliases,
    ssids: place.ssids,
    ha_zone: place.ha_zone,
    paused: place.paused,
    ...overrides,
  };
}

function coordinate(raw: string, bound: number): number | null {
  const trimmed = raw.trim();
  if (!/^-?\d{1,3}(\.\d{1,5})?$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return Math.abs(value) <= bound ? value : null;
}

/** One entry per line. Aliases are trimmed; SSIDs are kept byte-exact, only blank lines drop. */
function lines(raw: string, trim: boolean): string[] {
  return raw
    .split("\n")
    .map((line) => (trim ? line.trim() : line))
    .filter((line) => line.trim() !== "");
}

/** Validate a draft locally; the server re-validates everything. */
function validate(draft: Draft): OwnerPlaceInput | string {
  const label = draft.label.trim();
  if (label === "" || label.length > 80) return "Name the place in 1 to 80 characters.";
  const lat = coordinate(draft.lat, 90);
  const lon = coordinate(draft.lon, 180);
  if (lat === null || lon === null) {
    return "Enter latitude (-90 to 90) and longitude (-180 to 180) with at most five decimals.";
  }
  const radius = Number(draft.radius.trim());
  if (!Number.isInteger(radius) || radius < 50 || radius > 2000) {
    return "Use a radius of 50 to 2000 metres.";
  }
  const aliases = lines(draft.aliases, true);
  if (aliases.length > 10 || aliases.some((alias) => alias.length > 80)) {
    return "Use at most 10 other names of up to 80 characters.";
  }
  if (new Set(aliases.map((alias) => alias.toLowerCase())).size !== aliases.length) {
    return "Each other name may appear once.";
  }
  const ssids = lines(draft.ssids, false);
  if (ssids.length > 20 || ssids.some((ssid) => ENCODER.encode(ssid).length > 32)) {
    return "Use at most 20 Wi-Fi network names of up to 32 bytes.";
  }
  if (new Set(ssids).size !== ssids.length) return "Each Wi-Fi network may appear once.";
  const zone = draft.zone.trim();
  if (zone !== "" && !ZONE_PATTERN.test(zone)) {
    return "Use a Home Assistant zone ID such as zone.office, or leave it empty.";
  }
  return {
    label,
    kind: draft.kind,
    lat,
    lon,
    radius_m: radius,
    aliases,
    ssids,
    ha_zone: zone === "" ? null : zone,
    paused: draft.paused,
  };
}

function failureMessage(error: unknown, action: string): string {
  if (error instanceof ApiError && error.code === "VERSION_CONFLICT") {
    return "This place changed since it was loaded. The list now shows the latest version; open it again to edit.";
  }
  if (error instanceof ApiError && error.code === "PLACE_CONFLICT") {
    return "Another place already uses this name or Wi-Fi network, or is already your home.";
  }
  return `Could not ${action}. Nothing was changed.`;
}

/**
 * Owner-only editor for the owner place registry. Saved places are not read by anything yet:
 * the commuting estimate, OwnTracks place labels and Wi-Fi presence still use their legacy
 * configuration until a later cutover moves them onto this registry. Coordinates and network
 * names stay here; no model or prompt receives them.
 */
export function HomePlacesPanel() {
  const places = useOwnerPlaces();
  const save = useSaveOwnerPlace();
  const remove = useDeleteOwnerPlace();
  const [editing, setEditing] = useState<OwnerPlace | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [confirmingRemove, setConfirmingRemove] = useState<string | null>(null);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));

  const reset = () => {
    setEditing(null);
    setDraft(EMPTY_DRAFT);
    setFieldError(null);
  };

  const startEdit = (place: OwnerPlace) => {
    save.reset();
    setEditing(place);
    setDraft(draftFrom(place));
    setFieldError(null);
  };

  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const result = validate(draft);
    if (typeof result === "string") {
      setFieldError(result);
      return;
    }
    setFieldError(null);
    save.mutate(
      editing === null
        ? { id: null, input: result }
        : { id: editing.id, input: result, expectedVersion: editing.version },
      {
        onSuccess: reset,
        onError: (error) => {
          // A stale edit cannot be retried as-is: leave edit mode so the next attempt starts
          // from the refreshed row rather than resubmitting the old version.
          if (error instanceof ApiError && error.code === "VERSION_CONFLICT") {
            setEditing(null);
            setDraft(EMPTY_DRAFT);
          }
        },
      },
    );
  };

  // Pausing or removing the place open in the form would leave it holding a stale version.
  const leaveEditIf = (place: OwnerPlace) => {
    if (editing?.id === place.id) reset();
  };

  const togglePause = (place: OwnerPlace) => {
    leaveEditIf(place);
    save.mutate({
      id: place.id,
      input: inputFrom(place, { paused: !place.paused }),
      expectedVersion: place.version,
    });
  };

  const confirmRemove = (place: OwnerPlace) => {
    setConfirmingRemove(null);
    leaveEditIf(place);
    remove.mutate({ id: place.id, expectedVersion: place.version });
  };

  return (
    <Panel title="Places" span={4} testId="home-places-panel">
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Name the places that matter. Saved places are not used yet: the commuting estimate,
          location history and Wi-Fi presence keep their current configuration until they move
          onto this list. A paused place is kept but will be ignored.
        </p>

        {places.isLoading ? (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            Loading places
          </p>
        ) : null}
        {places.isError ? (
          <div className="flex flex-wrap items-center gap-3" aria-live="polite">
            <p className="text-sm text-destructive" data-testid="home-places-error">
              Couldn&apos;t load your places. Nothing has changed.
            </p>
            <Button type="button" variant="outline" size="sm" onClick={() => void places.refetch()}>
              Reload places
            </Button>
          </div>
        ) : null}
        {places.data && places.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">No places yet.</p>
        ) : null}
        {remove.isError ? (
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm text-destructive" role="alert">
              {failureMessage(remove.error, "remove the place")}
            </p>
            <Button type="button" variant="ghost" size="sm" onClick={() => remove.reset()}>
              Dismiss
            </Button>
          </div>
        ) : null}
        {places.data && places.data.length > 0 ? (
          <ul className="divide-y divide-border" data-testid="home-places-list">
            {places.data.map((place) => (
              <li key={place.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                <span className="font-medium">{place.label}</span>
                <span className="text-muted-foreground">
                  {KIND_CHOICES.find((choice) => choice.value === place.kind)?.name ?? place.kind}
                  {" · "}
                  {place.radius_m} m
                  {place.ssids.length > 0
                    ? ` · ${place.ssids.length} Wi-Fi network${place.ssids.length === 1 ? "" : "s"}`
                    : ""}
                </span>
                {place.paused ? (
                  <span className="rounded border border-border px-1.5 text-xs text-muted-foreground">
                    Paused
                  </span>
                ) : null}
                <span className="ml-auto flex gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-label={`Edit ${place.label}`}
                    onClick={() => startEdit(place)}
                  >
                    Edit
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={save.isPending}
                    aria-label={`${place.paused ? "Resume" : "Pause"} ${place.label}`}
                    onClick={() => togglePause(place)}
                  >
                    {place.paused ? "Resume" : "Pause"}
                  </Button>
                  {confirmingRemove === place.id ? (
                    <>
                      <Button
                        type="button"
                        variant="destructive"
                        size="sm"
                        disabled={remove.isPending}
                        aria-label={`Confirm removing ${place.label}`}
                        onClick={() => confirmRemove(place)}
                      >
                        Remove for good
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => setConfirmingRemove(null)}
                      >
                        Keep
                      </Button>
                    </>
                  ) : (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={remove.isPending}
                      aria-label={`Remove ${place.label}`}
                      onClick={() => {
                        remove.reset();
                        setConfirmingRemove(place.id);
                      }}
                    >
                      Remove
                    </Button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        ) : null}

        <form
          className="space-y-3"
          noValidate
          onSubmit={onSubmit}
          aria-label={editing ? `Edit ${editing.label}` : "Add a place"}
        >
          <p className="text-sm font-medium">{editing ? `Editing ${editing.label}` : "Add a place"}</p>
          <div className="grid gap-3 sm:grid-cols-4">
            <div className="space-y-1.5 sm:col-span-2">
              <label className="text-sm font-medium" htmlFor="home-place-label">
                Name
              </label>
              <Input
                id="home-place-label"
                maxLength={80}
                placeholder="Office"
                value={draft.label}
                onChange={(event) => set("label", event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="home-place-kind">
                Kind
              </label>
              <select
                id="home-place-kind"
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
                value={draft.kind}
                onChange={(event) => set("kind", event.target.value as OwnerPlaceKind)}
              >
                {KIND_CHOICES.map((choice) => (
                  <option key={choice.value} value={choice.value}>
                    {choice.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="home-place-radius">
                Radius (m)
              </label>
              <Input
                id="home-place-radius"
                inputMode="numeric"
                value={draft.radius}
                onChange={(event) => set("radius", event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="home-place-lat">
                Centre latitude
              </label>
              <Input
                id="home-place-lat"
                inputMode="decimal"
                autoComplete="off"
                value={draft.lat}
                onChange={(event) => set("lat", event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="home-place-lon">
                Centre longitude
              </label>
              <Input
                id="home-place-lon"
                inputMode="decimal"
                autoComplete="off"
                value={draft.lon}
                onChange={(event) => set("lon", event.target.value)}
              />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <label className="text-sm font-medium" htmlFor="home-place-zone">
                Home Assistant zone (optional)
              </label>
              <Input
                id="home-place-zone"
                autoComplete="off"
                spellCheck={false}
                placeholder="zone.office"
                value={draft.zone}
                onChange={(event) => set("zone", event.target.value)}
              />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <label className="text-sm font-medium" htmlFor="home-place-ssids">
                Wi-Fi networks, one per line (exact, case-sensitive)
              </label>
              <textarea
                id="home-place-ssids"
                rows={2}
                spellCheck={false}
                className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm"
                value={draft.ssids}
                onChange={(event) => set("ssids", event.target.value)}
              />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <label className="text-sm font-medium" htmlFor="home-place-aliases">
                Other names, one per line
              </label>
              <textarea
                id="home-place-aliases"
                rows={2}
                className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm"
                value={draft.aliases}
                onChange={(event) => set("aliases", event.target.value)}
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.paused}
              onChange={(event) => set("paused", event.target.checked)}
            />
            Paused (kept, but ignored)
          </label>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? "Saving place..." : editing ? "Save changes" : "Add place"}
            </Button>
            {editing ? (
              <Button type="button" variant="outline" onClick={reset}>
                Cancel
              </Button>
            ) : null}
            {fieldError ? (
              <p className="text-sm text-destructive" role="alert">
                {fieldError}
              </p>
            ) : null}
            {save.isError ? (
              <p className="text-sm text-destructive" role="alert">
                {failureMessage(save.error, "save the place")}
              </p>
            ) : null}
          </div>
        </form>
      </div>
    </Panel>
  );
}
