import { useState } from "react";

import type { HomeSafetyRole } from "@/api/home-safety-watch.ts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useDeleteHomeSafetyWatch,
  useHomeSafetyWatch,
  useSaveHomeSafetyWatch,
} from "@/hooks/use-home-safety-watch.ts";

import { Panel } from "@/components/ui/atoms";

const ROLE_CHOICES: ReadonlyArray<{ value: HomeSafetyRole; name: string }> = [
  { value: "leak", name: "Water leak" },
  { value: "smoke", name: "Smoke" },
  { value: "co", name: "Carbon monoxide" },
  { value: "gas", name: "Gas" },
];

const ENTITY_PATTERN = /^binary_sensor\.[a-z0-9_]+$/;

/**
 * Owner-only declaration of which binary sensors guard against a life-safety hazard.
 * The safety sentinel alerts only on the sensors listed here and words every alert with the
 * label typed here, never with Home Assistant's own name for the device.
 */
export function HomeSafetyWatchPanel() {
  const watch = useHomeSafetyWatch();
  const save = useSaveHomeSafetyWatch();
  const remove = useDeleteHomeSafetyWatch();
  const [entityId, setEntityId] = useState("");
  const [role, setRole] = useState<HomeSafetyRole>("leak");
  const [label, setLabel] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);

  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedEntity = entityId.trim();
    const trimmedLabel = label.trim();
    if (!ENTITY_PATTERN.test(trimmedEntity)) {
      setFieldError("Use a binary sensor ID such as binary_sensor.kitchen_leak.");
      return;
    }
    if (trimmedLabel === "" || trimmedLabel.length > 80) {
      setFieldError("Name the sensor in 1 to 80 characters.");
      return;
    }
    setFieldError(null);
    save.mutate(
      { entity_id: trimmedEntity, role, owner_label: trimmedLabel },
      {
        onSuccess: () => {
          setEntityId("");
          setLabel("");
        },
      },
    );
  };

  return (
    <Panel title="Safety sensors" span={4} testId="home-safety-watch-panel">
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Declare the sensors that guard against a leak, smoke, carbon monoxide or gas. When one
          stays on for a minute you are alerted immediately, using the name you give here.
        </p>

        {watch.isLoading ? (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            Loading safety sensors
          </p>
        ) : null}
        {watch.isError ? (
          <div className="flex flex-wrap items-center gap-3" aria-live="polite">
            <p className="text-sm text-destructive" data-testid="home-safety-watch-error">
              Couldn&apos;t load your safety sensors. Alerts still use the list already saved.
            </p>
            <Button type="button" variant="outline" size="sm" onClick={() => void watch.refetch()}>
              Reload safety sensors
            </Button>
          </div>
        ) : null}
        {watch.data && watch.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">No safety sensors declared yet.</p>
        ) : null}
        {remove.isError ? (
          <p className="text-sm text-destructive" data-testid="home-safety-watch-remove-error">
            Could not remove the sensor. It is still being watched.
          </p>
        ) : null}
        {watch.data ? (
          <ul className="space-y-2" data-testid="home-safety-watch-list">
            {watch.data.map((entry) => (
              <li
                key={`${entry.entity_id}/${entry.role}`}
                className="flex flex-wrap items-center gap-3 text-sm"
              >
                <span className="font-medium">{entry.owner_label}</span>
                <span className="text-muted-foreground">
                  {ROLE_CHOICES.find((choice) => choice.value === entry.role)?.name ?? entry.role}
                </span>
                <code className="text-xs">{entry.entity_id}</code>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={remove.isPending}
                  aria-label={`Stop watching ${entry.owner_label}`}
                  onClick={() => remove.mutate({ entityId: entry.entity_id, role: entry.role })}
                >
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        ) : null}

        <form className="space-y-3" noValidate onSubmit={onSubmit}>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="home-safety-entity">
                Sensor entity ID
              </label>
              <Input
                id="home-safety-entity"
                autoComplete="off"
                spellCheck={false}
                placeholder="binary_sensor.kitchen_leak"
                value={entityId}
                onChange={(event) => setEntityId(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="home-safety-role">
                Hazard
              </label>
              <select
                id="home-safety-role"
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
                value={role}
                onChange={(event) => setRole(event.target.value as HomeSafetyRole)}
              >
                {ROLE_CHOICES.map((choice) => (
                  <option key={choice.value} value={choice.value}>
                    {choice.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="home-safety-label">
                Name in alerts
              </label>
              <Input
                id="home-safety-label"
                maxLength={80}
                placeholder="Kitchen leak sensor"
                value={label}
                onChange={(event) => setLabel(event.target.value)}
              />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? "Saving sensor..." : "Save sensor"}
            </Button>
            {fieldError ? (
              <p className="text-sm text-destructive" role="alert">
                {fieldError}
              </p>
            ) : null}
            {save.isError ? (
              <p className="text-sm text-destructive" role="alert">
                Could not save the sensor. Check the details and try again.
              </p>
            ) : null}
          </div>
        </form>
      </div>
    </Panel>
  );
}
