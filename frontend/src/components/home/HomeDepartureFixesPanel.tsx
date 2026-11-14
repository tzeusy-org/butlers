import { useState } from "react";

import type { HomeDepartureFix, HomeDepartureWatchItem } from "@/api/home-departure-fixes.ts";
import { Panel } from "@/components/ui/atoms";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import {
  useDeleteDepartureFix,
  useHomeDepartureFixes,
  useHomeDepartureWatch,
  useSaveDepartureFix,
} from "@/hooks/use-home-departure-fixes.ts";

/** The calls a fix may be: the same allowlist the route enforces, none needing extra data. */
const FIX_CALLS = [
  "light.turn_off",
  "light.turn_on",
  "switch.turn_off",
  "switch.turn_on",
  "fan.turn_off",
  "fan.turn_on",
  "humidifier.turn_off",
  "humidifier.turn_on",
  "cover.close_cover",
  "cover.open_cover",
  "lock.lock",
  "lock.unlock",
  "scene.turn_on",
  "script.turn_on",
] as const;

const TARGET_ENTITY = /^[a-z0-9_]+\.[a-z0-9_]+$/;

const RISK_WORDS: Record<HomeDepartureFix["risk"], string> = {
  safe: "no approval class",
  reversible: "reversible",
  consequential: "consequential",
  protected: "protected",
};

function FixRow({ item, fix }: { item: HomeDepartureWatchItem; fix: HomeDepartureFix | undefined }) {
  const save = useSaveDepartureFix();
  const remove = useDeleteDepartureFix();
  const [call, setCall] = useState("");
  const [target, setTarget] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const labelId = `fix-${item.entity_id}`;

  const onSave = () => {
    const trimmed = target.trim();
    if (!(FIX_CALLS as readonly string[]).includes(call)) {
      setFieldError("Choose what the fix does.");
      return;
    }
    if (!TARGET_ENTITY.test(trimmed) || trimmed.split(".")[0] !== call.split(".")[0]) {
      setFieldError("Name one entity of the same kind, such as light.garage.");
      return;
    }
    setFieldError(null);
    const [domain, service] = call.split(".");
    save.mutate(
      { entity_id: item.entity_id, domain, service, target_entity_id: trimmed, data: {} },
      {
        onSuccess: () => {
          setCall("");
          setTarget("");
        },
      },
    );
  };

  return (
    <li className="space-y-2 text-sm" data-testid={`home-departure-fix-${item.entity_id}`}>
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-medium">{item.owner_label}</span>
        <code className="text-xs">{item.entity_id}</code>
      </div>
      {fix ? (
        <div className="flex flex-wrap items-center gap-3">
          <span>
            Offer <code className="text-xs">{`${fix.domain}.${fix.service}`}</code> on{" "}
            <code className="text-xs">{fix.target_entity_id}</code>
          </span>
          <span className="text-muted-foreground">{RISK_WORDS[fix.risk]}, always needs your approval</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={remove.isPending}
            aria-label={`Remove the fix for ${item.owner_label}`}
            onClick={() => remove.mutate(item.entity_id)}
          >
            Remove fix
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1" htmlFor={`${labelId}-call`}>
            <span className="text-xs text-muted-foreground">Fix action for {item.owner_label}</span>
            <select
              id={`${labelId}-call`}
              className="h-9 rounded-md border bg-background px-2"
              value={call}
              onChange={(event) => setCall(event.target.value)}
            >
              <option value="">No fix</option>
              {FIX_CALLS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1" htmlFor={`${labelId}-target`}>
            <span className="text-xs text-muted-foreground">Entity to act on ({item.owner_label})</span>
            <Input
              id={`${labelId}-target`}
              value={target}
              onChange={(event) => setTarget(event.target.value)}
            />
          </label>
          <Button
            type="button"
            size="sm"
            disabled={save.isPending}
            aria-label={`Save the fix for ${item.owner_label}`}
            onClick={onSave}
          >
            Save fix
          </Button>
        </div>
      )}
      {fieldError || save.isError || remove.isError ? (
        <p className="text-sm text-destructive" role="alert">
          {fieldError ?? "Could not change the fix. The previous setting still applies."}
        </p>
      ) : null}
    </li>
  );
}

/**
 * Owner-only departure fixes: for each watched item, the one exact call to offer when you leave
 * with it not secure. Nothing runs from here: an offered fix waits for your approval on
 * Approvals, and is withdrawn if you return first.
 */
export function HomeDepartureFixesPanel() {
  const watch = useHomeDepartureWatch();
  const fixes = useHomeDepartureFixes();
  const loading = watch.isLoading || fixes.isLoading;
  const failed = watch.isError || fixes.isError;

  return (
    <Panel title="Departure fixes" span={4} testId="home-departure-fixes-panel">
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          A fix is offered for your approval only when you leave with that item not secure. It never
          runs on its own, and it is withdrawn if you come back first.
        </p>
        {loading ? (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            Loading departure fixes
          </p>
        ) : null}
        {failed ? (
          <SourceDegradedNote
            label="Departure fixes"
            detail="couldn't load; fixes already saved still apply"
            testId="home-departure-fixes-error"
            onRetry={() => {
              void watch.refetch();
              void fixes.refetch();
            }}
          />
        ) : null}
        {watch.data && fixes.data ? (
          watch.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nothing is on the departure check yet, so there is nothing to fix.
            </p>
          ) : (
            <ul className="space-y-4" data-testid="home-departure-fix-list">
              {watch.data.map((item) => (
                <FixRow
                  key={item.entity_id}
                  item={item}
                  fix={fixes.data.find((fix) => fix.entity_id === item.entity_id)}
                />
              ))}
            </ul>
          )
        ) : null}
      </div>
    </Panel>
  );
}
