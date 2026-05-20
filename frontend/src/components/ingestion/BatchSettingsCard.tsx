/**
 * BatchSettingsCard — editable flush_interval_s for batch connectors.
 *
 * Rendered only for telegram_user_client and whatsapp_user_client connector
 * types. Changes are submitted via PATCH /connectors/:type/:identity/settings
 * and take effect on the connector's next flush scanner cycle (no restart
 * required).
 */

import { useState } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import type { ConnectorDetail } from "@/api/types.ts";
import type { useUpdateConnectorSettings } from "@/hooks/use-ingestion";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const FLUSH_INTERVAL_MIN = 60;
const FLUSH_INTERVAL_MAX = 7200;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface BatchSettingsCardProps {
  connector: ConnectorDetail;
  settingsMutation: ReturnType<typeof useUpdateConnectorSettings>;
}

// ---------------------------------------------------------------------------
// BatchSettingsCard
// ---------------------------------------------------------------------------

export function BatchSettingsCard({
  connector,
  settingsMutation,
}: BatchSettingsCardProps) {
  // Read the dashboard-set flush_interval_s from connector.settings, if any.
  const dashboardSettings = (connector.settings as Record<string, unknown> | null) ?? {};
  const dashboardValue = dashboardSettings.flush_interval_s as number | undefined;

  // "custom" = a value has been explicitly set via the dashboard.
  // "default" = the connector is using the env var / compiled-in default.
  const isCustom = dashboardValue !== undefined && dashboardValue !== null;
  const displayedValue = dashboardValue ?? 1800; // 1800s is the connector default

  const [draft, setDraft] = useState<string | null>(null);

  const draftNum = draft !== null ? parseInt(draft, 10) : null;
  const validationError =
    draft !== null
      ? isNaN(draftNum!)
        ? "Must be a number"
        : draftNum! < FLUSH_INTERVAL_MIN
          ? `Minimum ${FLUSH_INTERVAL_MIN} seconds`
          : draftNum! > FLUSH_INTERVAL_MAX
            ? `Maximum ${FLUSH_INTERVAL_MAX} seconds`
            : null
      : null;

  const isDirty = draft !== null && draftNum !== displayedValue;
  const canSave = isDirty && validationError === null;

  function handleSave() {
    if (!canSave) return;
    settingsMutation.mutate(
      { flush_interval_s: draftNum! },
      {
        onSuccess: () => {
          setDraft(null);
        },
      },
    );
  }

  function handleCancel() {
    setDraft(null);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Batch Settings</CardTitle>
        <CardDescription>
          Configures how long the connector buffers messages before flushing a
          batch. Changes take effect on the next flush scanner cycle (within 60
          seconds).
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <label className="text-sm font-medium">Flush interval</label>
              <Badge
                variant={isCustom ? "default" : "secondary"}
                className="text-xs"
                data-testid="flush-interval-badge"
              >
                {isCustom ? "custom" : "default"}
              </Badge>
            </div>
            <div className="flex items-center gap-2">
              <Input
                type="number"
                min={FLUSH_INTERVAL_MIN}
                max={FLUSH_INTERVAL_MAX}
                step={60}
                value={draft ?? displayedValue}
                onChange={(e) => setDraft(e.target.value)}
                className="w-28 font-mono text-sm"
                data-testid="flush-interval-input"
              />
              <span className="text-sm text-muted-foreground">seconds</span>
              {canSave && (
                <Button
                  size="sm"
                  onClick={handleSave}
                  disabled={settingsMutation.isPending}
                  data-testid="flush-interval-save-btn"
                >
                  {settingsMutation.isPending ? "Saving..." : "Save"}
                </Button>
              )}
              {draft !== null && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={handleCancel}
                  disabled={settingsMutation.isPending}
                  data-testid="flush-interval-cancel-btn"
                >
                  Cancel
                </Button>
              )}
            </div>
            {validationError && (
              <p
                className="text-xs text-destructive"
                data-testid="flush-interval-error"
              >
                {validationError}
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              Valid range: {FLUSH_INTERVAL_MIN}–{FLUSH_INTERVAL_MAX} seconds (
              {FLUSH_INTERVAL_MIN / 60}–{FLUSH_INTERVAL_MAX / 60} minutes)
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
