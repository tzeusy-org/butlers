import { useState } from "react";

import type {
  FreezerUnit,
  HomeAwayDoorEntry,
  HomeAwaySafetyStatus,
  HomeFreezerInput,
} from "@/api/home-away-safety.ts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useArmHomeAwayDoor,
  useClearHomeBlindDwell,
  useDeleteHomeFreezer,
  useDisarmHomeAwayDoor,
  useHomeAwaySafety,
  useSaveHomeBlindDwell,
  useSaveHomeFreezer,
} from "@/hooks/use-home-away-safety.ts";

import { Panel } from "@/components/ui/atoms";

const FREEZER_ENTITY = /^sensor\.[a-z0-9_]+$/;

/** Validation bounds only: none of these is a suggested value, and no field is pre-filled. */
const FREEZER_FIELDS = [
  { key: "max_c", label: "Alert above (°C)", min: -30, max: 0, integer: false },
  { key: "hysteresis_c", label: "Clear this far below (°C)", min: 0.5, max: 10, integer: false },
  { key: "dwell_minutes", label: "Warm for (minutes)", min: 5, max: 240, integer: true },
  {
    key: "max_reading_age_minutes",
    label: "Oldest usable reading (minutes)",
    min: 10,
    max: 360,
    integer: true,
  },
] as const;

type FreezerNumberKey = (typeof FREEZER_FIELDS)[number]["key"];

function parseBounded(raw: string, min: number, max: number, integer: boolean): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < min || value > max) return null;
  if (integer && !Number.isInteger(value)) return null;
  return value;
}

function StatusLine({ status }: { status: HomeAwaySafetyStatus }) {
  const word = (value: string) => (value === "ok" ? "on" : "unconfigured, silent");
  return (
    <ul className="space-y-1 text-sm" data-testid="home-away-safety-status">
      <li>Freezer alerts: {word(status.freezer)}</li>
      <li>Door left open while away: {word(status.away_door)}</li>
      <li>Home cannot see while away: {word(status.blind)}</li>
    </ul>
  );
}

function FreezerSection({ freezers }: { freezers: HomeFreezerInput[] }) {
  const save = useSaveHomeFreezer();
  const remove = useDeleteHomeFreezer();
  const [entityId, setEntityId] = useState("");
  const [label, setLabel] = useState("");
  // No default unit: a wrong one makes every reading unknown and the freezer silently unwatched.
  const [unit, setUnit] = useState<FreezerUnit | "">("");
  const [numbers, setNumbers] = useState<Record<FreezerNumberKey, string>>({
    max_c: "",
    hysteresis_c: "",
    dwell_minutes: "",
    max_reading_age_minutes: "",
  });
  const [fieldError, setFieldError] = useState<string | null>(null);

  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedEntity = entityId.trim();
    const trimmedLabel = label.trim();
    if (!FREEZER_ENTITY.test(trimmedEntity)) {
      setFieldError("Use a sensor ID such as sensor.garage_freezer_temperature.");
      return;
    }
    if (trimmedLabel === "" || trimmedLabel.length > 80) {
      setFieldError("Name the freezer in 1 to 80 characters.");
      return;
    }
    if (unit === "") {
      setFieldError("Choose the unit the sensor reports in.");
      return;
    }
    const parsed: Partial<Record<FreezerNumberKey, number>> = {};
    for (const field of FREEZER_FIELDS) {
      const value = parseBounded(numbers[field.key], field.min, field.max, field.integer);
      if (value === null) {
        setFieldError(`${field.label}: enter a value from ${field.min} to ${field.max}.`);
        return;
      }
      parsed[field.key] = value;
    }
    setFieldError(null);
    save.mutate(
      {
        entity_id: trimmedEntity,
        owner_label: trimmedLabel,
        unit,
        ...(parsed as Record<FreezerNumberKey, number>),
      },
      {
        onSuccess: () => {
          setEntityId("");
          setLabel("");
          setUnit("");
          setNumbers({ max_c: "", hysteresis_c: "", dwell_minutes: "", max_reading_age_minutes: "" });
        },
      },
    );
  };

  return (
    <section className="space-y-3" aria-labelledby="home-away-freezers">
      <h3 id="home-away-freezers" className="text-sm font-semibold">
        Freezers
      </h3>
      {freezers.length === 0 ? (
        <p className="text-sm text-muted-foreground">No freezer declared. Freezer alerts are off.</p>
      ) : (
        <ul className="space-y-2" data-testid="home-away-freezer-list">
          {freezers.map((freezer) => (
            <li key={freezer.entity_id} className="flex flex-wrap items-center gap-3 text-sm">
              <span className="font-medium">{freezer.owner_label}</span>
              <span className="text-muted-foreground">
                above {freezer.max_c} °C for {freezer.dwell_minutes} min, clears at{" "}
                {Number((freezer.max_c - freezer.hysteresis_c).toFixed(2))} °C
              </span>
              <code className="text-xs">{freezer.entity_id}</code>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={remove.isPending}
                aria-label={`Stop watching ${freezer.owner_label}`}
                onClick={() => remove.mutate(freezer.entity_id)}
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
      {remove.isError ? (
        <p className="text-sm text-destructive" role="alert">
          Could not remove the freezer. It is still being watched.
        </p>
      ) : null}
      <form className="space-y-3" noValidate onSubmit={onSubmit}>
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor="home-away-freezer-entity">
              Temperature sensor ID
            </label>
            <Input
              id="home-away-freezer-entity"
              autoComplete="off"
              spellCheck={false}
              value={entityId}
              onChange={(event) => setEntityId(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor="home-away-freezer-label">
              Name in alerts
            </label>
            <Input
              id="home-away-freezer-label"
              maxLength={80}
              value={label}
              onChange={(event) => setLabel(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor="home-away-freezer-unit">
              Sensor reports in
            </label>
            <select
              id="home-away-freezer-unit"
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
              value={unit}
              onChange={(event) => setUnit(event.target.value as FreezerUnit | "")}
            >
              <option value="" disabled>
                Choose unit
              </option>
              <option value="°C">°C</option>
              <option value="°F">°F</option>
            </select>
          </div>
          {FREEZER_FIELDS.map((field) => (
            <div className="space-y-1.5" key={field.key}>
              <label className="text-sm font-medium" htmlFor={`home-away-freezer-${field.key}`}>
                {field.label}
              </label>
              <Input
                id={`home-away-freezer-${field.key}`}
                inputMode="decimal"
                value={numbers[field.key]}
                onChange={(event) =>
                  setNumbers((current) => ({ ...current, [field.key]: event.target.value }))
                }
              />
            </div>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? "Saving freezer..." : "Save freezer"}
          </Button>
          {fieldError ? (
            <p className="text-sm text-destructive" role="alert">
              {fieldError}
            </p>
          ) : null}
          {save.isError ? (
            <p className="text-sm text-destructive" role="alert">
              Could not save the freezer. Check the values and try again.
            </p>
          ) : null}
        </div>
      </form>
    </section>
  );
}

function DoorRow({ door }: { door: HomeAwayDoorEntry }) {
  const arm = useArmHomeAwayDoor();
  const disarm = useDisarmHomeAwayDoor();
  const [minutes, setMinutes] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const inputId = `home-away-door-${door.entity_id}`;

  const onArm = () => {
    const value = parseBounded(minutes, 30, 1440, true);
    if (value === null) {
      setFieldError("Enter whole minutes from 30 to 1440.");
      return;
    }
    setFieldError(null);
    arm.mutate({ entityId: door.entity_id, minutes: value }, { onSuccess: () => setMinutes("") });
  };

  return (
    <li className="flex flex-wrap items-center gap-3 text-sm">
      <span className="font-medium">{door.owner_label}</span>
      <span className="text-muted-foreground">
        {door.away_alert_after_minutes === null
          ? "not armed"
          : `alert after ${door.away_alert_after_minutes} min away`}
      </span>
      <label className="sr-only" htmlFor={inputId}>
        Minutes before alerting for {door.owner_label}
      </label>
      <Input
        id={inputId}
        className="w-24"
        inputMode="numeric"
        value={minutes}
        onChange={(event) => setMinutes(event.target.value)}
      />
      <Button type="button" size="sm" disabled={arm.isPending} onClick={onArm}>
        {door.away_alert_after_minutes === null ? "Arm" : "Change"}
      </Button>
      {door.away_alert_after_minutes !== null ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disarm.isPending}
          aria-label={`Disarm ${door.owner_label}`}
          onClick={() => disarm.mutate(door.entity_id)}
        >
          Disarm
        </Button>
      ) : null}
      {fieldError || arm.isError || disarm.isError ? (
        <p className="w-full text-sm text-destructive" role="alert">
          {fieldError ?? "Could not change this item. Its previous setting still applies."}
        </p>
      ) : null}
    </li>
  );
}

function BlindSection({
  minutes,
  sensingGroup,
}: {
  minutes: number | null;
  sensingGroup: HomeAwaySafetyStatus["sensing_group"];
}) {
  const save = useSaveHomeBlindDwell();
  const clear = useClearHomeBlindDwell();
  const [value, setValue] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);

  const onSave = () => {
    const parsed = parseBounded(value, 5, 1440, true);
    if (parsed === null) {
      setFieldError("Enter whole minutes from 5 to 1440.");
      return;
    }
    setFieldError(null);
    save.mutate(parsed, { onSuccess: () => setValue("") });
  };

  return (
    <section className="space-y-3" aria-labelledby="home-away-blind">
      <h3 id="home-away-blind" className="text-sm font-semibold">
        When Home cannot see
      </h3>
      <p className="text-sm text-muted-foreground" data-testid="home-away-sensing-group">
        {sensingGroup === "registered"
          ? "Home's sensing group is registered."
          : "Home's sensing group is not registered yet. It is set up once during activation, " +
            "not here, so this alert stays off until then."}
      </p>
      <p className="text-sm">
        {minutes === null
          ? "No wait set. This alert is off."
          : `Tell me after ${minutes} min without sight while I am away.`}
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm font-medium" htmlFor="home-away-blind-minutes">
          Minutes without sight
        </label>
        <Input
          id="home-away-blind-minutes"
          className="w-24"
          inputMode="numeric"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
        <Button type="button" size="sm" disabled={save.isPending} onClick={onSave}>
          Save wait
        </Button>
        {minutes !== null ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={clear.isPending}
            onClick={() => clear.mutate()}
          >
            Turn off
          </Button>
        ) : null}
      </div>
      {fieldError || save.isError || clear.isError ? (
        <p className="text-sm text-destructive" role="alert">
          {fieldError ?? "Could not change the wait. The previous setting still applies."}
        </p>
      ) : null}
    </section>
  );
}

/**
 * Owner-only away-safety settings: freezers, which departure items alert while away, and how
 * long Home may be unable to see before it says so. Every value is typed by the owner; a
 * missing one leaves that alert off and visibly unconfigured.
 */
export function HomeAwaySafetyPanel() {
  const config = useHomeAwaySafety();

  return (
    <Panel title="Away safety" span={4} testId="home-away-safety-panel">
      <div className="space-y-6">
        {config.isLoading ? (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            Loading away safety
          </p>
        ) : null}
        {config.isError ? (
          <div className="flex flex-wrap items-center gap-3" aria-live="polite">
            <p className="text-sm text-destructive" data-testid="home-away-safety-error">
              Couldn&apos;t load away safety. Alerts still use the settings already saved.
            </p>
            <Button type="button" variant="outline" size="sm" onClick={() => void config.refetch()}>
              Reload away safety
            </Button>
          </div>
        ) : null}
        {config.data ? (
          <>
            <StatusLine status={config.data.status} />
            <FreezerSection freezers={config.data.freezers} />
            <section className="space-y-3" aria-labelledby="home-away-doors">
              <h3 id="home-away-doors" className="text-sm font-semibold">
                Left open while away
              </h3>
              {config.data.doors.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Nothing is on the departure check yet, so there is nothing to arm.
                </p>
              ) : (
                <ul className="space-y-2" data-testid="home-away-door-list">
                  {config.data.doors.map((door) => (
                    <DoorRow key={door.entity_id} door={door} />
                  ))}
                </ul>
              )}
            </section>
            <BlindSection
              minutes={config.data.blind_after_minutes}
              sensingGroup={config.data.status.sensing_group}
            />
          </>
        ) : null}
      </div>
    </Panel>
  );
}
