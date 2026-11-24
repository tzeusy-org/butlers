import { useState } from "react";

import { Panel } from "@/components/ui/atoms";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import {
  useDeleteCriticalEntity,
  useHomeCriticalEntities,
  useSaveCriticalEntity,
} from "@/hooks/use-home-critical-entities.ts";

const ENTITY_PATTERN = /^[a-z0-9_]+\.[a-z0-9_]+$/;

/**
 * Owner-only critical entities: a call that touches one always waits for your own approval, and no
 * saved rule, suggestion or goal permission can approve it. Marking one runs nothing. The panel
 * states what the gate cannot see (RFC 0028 Amendment 1, item 2a) so a listed item is not read as
 * a guarantee about Home Assistant itself.
 */
export function HomeCriticalEntitiesPanel() {
  const registry = useHomeCriticalEntities();
  const save = useSaveCriticalEntity();
  const remove = useDeleteCriticalEntity();
  const [entityId, setEntityId] = useState("");
  const [label, setLabel] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);

  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedEntity = entityId.trim();
    const trimmedLabel = label.trim();
    if (!ENTITY_PATTERN.test(trimmedEntity)) {
      setFieldError("Use one entity ID such as switch.freezer.");
      return;
    }
    if (trimmedLabel === "" || trimmedLabel.length > 80) {
      setFieldError("Name it in 1 to 80 characters.");
      return;
    }
    setFieldError(null);
    save.mutate(
      { entity_id: trimmedEntity, owner_label: trimmedLabel },
      {
        onSuccess: () => {
          setEntityId("");
          setLabel("");
        },
      },
    );
  };

  return (
    <Panel title="Critical entities" span={4} testId="home-critical-entities-panel">
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Anything Butlers would do to a critical entity waits for your own approval of that exact
          call. While any entity is critical, calls that cover a whole area or everything, scenes,
          scripts, automations and services Home does not recognise also wait. Reading a device
          never does.
        </p>
        <p className="text-sm text-muted-foreground" data-testid="home-critical-entities-limits">
          This covers only calls Butlers sends. Home Assistant automations that fire on their own
          triggers, and changes made outside Butlers, are not covered. A helper that wraps a device
          without listing it is covered only if you mark the helper itself.
        </p>

        {registry.isLoading ? (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            Loading critical entities
          </p>
        ) : null}
        {registry.isError ? (
          <SourceDegradedNote
            label="Critical entities"
            detail="couldn't load; Home treats every call as critical until it can read them"
            testId="home-critical-entities-error"
            onRetry={() => void registry.refetch()}
          />
        ) : null}
        {registry.data && registry.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No entity is marked critical, so calls follow the usual approval rules.
          </p>
        ) : null}
        {remove.isError ? (
          <p className="text-sm text-destructive" role="alert">
            Could not unmark the entity. It is still critical.
          </p>
        ) : null}
        {registry.data && registry.data.length > 0 ? (
          <>
            <ul className="space-y-2" data-testid="home-critical-entities-list">
              {registry.data.map((entry) => (
                <li key={entry.entity_id} className="flex flex-wrap items-center gap-3 text-sm">
                  <span className="font-medium">{entry.owner_label}</span>
                  <code className="text-xs">{entry.entity_id}</code>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={remove.isPending}
                    aria-label={`Unmark ${entry.owner_label} as critical`}
                    onClick={() => remove.mutate(entry.entity_id)}
                  >
                    Unmark
                  </Button>
                </li>
              ))}
            </ul>
            <p className="text-xs text-muted-foreground">
              Unmarking applies to later calls. A call already waiting for approval keeps waiting.
            </p>
          </>
        ) : null}

        <form className="space-y-3" noValidate onSubmit={onSubmit}>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="home-critical-entity">
                Entity ID
              </label>
              <Input
                id="home-critical-entity"
                autoComplete="off"
                spellCheck={false}
                placeholder="switch.freezer"
                value={entityId}
                onChange={(event) => setEntityId(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="home-critical-label">
                Your name for it
              </label>
              <Input
                id="home-critical-label"
                maxLength={80}
                placeholder="Freezer plug"
                value={label}
                onChange={(event) => setLabel(event.target.value)}
              />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? "Saving..." : "Mark critical"}
            </Button>
            {fieldError ? (
              <p className="text-sm text-destructive" role="alert">
                {fieldError}
              </p>
            ) : null}
            {save.isError ? (
              <p className="text-sm text-destructive" role="alert">
                Could not mark the entity. Check the details and try again.
              </p>
            ) : null}
          </div>
        </form>
      </div>
    </Panel>
  );
}
