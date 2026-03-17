import { useState } from "react";
import { ChevronDown, ChevronUp, Info, Loader2, FlaskConical, Check, X, RefreshCw, Pencil } from "lucide-react";
import { toast } from "sonner";

import type { ComplexityTier, ModelCatalogCreate, ModelCatalogEntry, ModelTestResult, UsageWindow } from "@/api/types.ts";
import { ComplexityBadge, COMPLEXITY_TIERS, complexityLabel } from "@/components/general/ComplexityBadge.tsx";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  useCreateModelCatalogEntry,
  useDeleteModelCatalogEntry,
  useModelCatalog,
  useTestModelCatalogEntry,
  useUpdateModelCatalogEntry,
  useSetModelTokenLimits,
  useResetModelUsage,
} from "@/hooks/use-model-catalog.ts";

// ---------------------------------------------------------------------------
// Token count formatting
// ---------------------------------------------------------------------------

/** Format token count as a short string: 142312 -> "142K", 1234567 -> "1.2M". */
function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

/** Format token count with commas: 142312 -> "142,312". */
function formatTokensExact(n: number): string {
  return n.toLocaleString();
}

// ---------------------------------------------------------------------------
// Progress bar color
// ---------------------------------------------------------------------------

/** Compute a Tailwind background-color class based on percent used (0-100+). */
function usageBarColor(percent: number): string {
  if (percent >= 100) return "bg-red-600";
  if (percent >= 85) return "bg-red-500";
  if (percent >= 60) return "bg-yellow-500";
  return "bg-emerald-500";
}

// ---------------------------------------------------------------------------
// UsageBar — mini horizontal progress bar with label, reset button, and tooltip
// ---------------------------------------------------------------------------

interface UsageBarProps {
  entryId: string;
  window: UsageWindow;
  used: number;
  limit: number | null;
  resetAt?: string | null;
  onLimitClick?: () => void;
}

function UsageBar({ entryId, window: usageWindow, used, limit, resetAt, onLimitClick }: UsageBarProps) {
  const resetMutation = useResetModelUsage();

  const percent = limit != null ? (used / limit) * 100 : null;
  const isBlocked = limit != null && used >= limit;
  const windowLabel = usageWindow === "24h" ? "Rolling 24h window" : "Rolling 30d window";

  // Tooltip lines
  const tooltipLines: string[] = [];
  if (limit != null) {
    tooltipLines.push(`${formatTokensExact(used)} / ${formatTokensExact(limit)} tokens`);
    tooltipLines.push(`${Math.round((used / limit) * 100)}% used · ${windowLabel}`);
  } else {
    tooltipLines.push(`${formatTokensExact(used)} tokens used`);
    tooltipLines.push(`No limit · ${windowLabel}`);
  }
  if (resetAt) {
    const resetDate = new Date(resetAt);
    const diffMs = Date.now() - resetDate.getTime();
    const diffH = diffMs / (1000 * 60 * 60);
    const relativeStr = diffH < 1
      ? `${Math.round(diffH * 60)}m ago`
      : diffH < 24
        ? `${Math.round(diffH)}h ago`
        : `${Math.round(diffH / 24)}d ago`;
    tooltipLines.push(`Last reset: ${relativeStr}`);
  }

  function handleReset(e: React.MouseEvent) {
    e.stopPropagation();
    resetMutation.mutate(
      { id: entryId, body: { window: usageWindow } },
      {
        onSuccess: () => toast.success(`Reset ${usageWindow} usage`),
        onError: (err) => toast.error(`Reset failed: ${err instanceof Error ? err.message : "Unknown error"}`),
      },
    );
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="flex flex-col gap-0.5 min-w-[110px]">
            {/* Text label row */}
            <div className="flex items-center gap-1">
              <span className="text-xs tabular-nums text-muted-foreground whitespace-nowrap">
                {formatTokens(used)} /{" "}
                <button
                  type="button"
                  className="underline decoration-dotted hover:text-foreground cursor-pointer"
                  title={limit != null ? "Click to edit limit" : "Click to set a limit"}
                  onClick={(e) => { e.stopPropagation(); onLimitClick?.(); }}
                >
                  {limit != null ? formatTokens(limit) : "-"}
                </button>
              </span>
              {isBlocked && (
                <Badge className="h-3.5 px-1 text-[9px] leading-none bg-red-600 text-white hover:bg-red-600">
                  BLOCKED
                </Badge>
              )}
              <button
                type="button"
                title={`Reset ${usageWindow} usage`}
                className="ml-auto h-3.5 w-3.5 flex items-center justify-center rounded hover:bg-muted text-muted-foreground hover:text-foreground disabled:opacity-30"
                onClick={handleReset}
                disabled={resetMutation.isPending}
              >
                {resetMutation.isPending ? (
                  <Loader2 className="h-2.5 w-2.5 animate-spin" />
                ) : (
                  <RefreshCw className="h-2.5 w-2.5" />
                )}
              </button>
            </div>
            {/* Progress bar (only when limit is set) */}
            {limit != null && percent != null && (
              <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${usageBarColor(percent)}`}
                  style={{ width: `${Math.min(percent, 100)}%` }}
                />
              </div>
            )}
          </div>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-64">
          <div className="space-y-0.5 text-xs">
            {tooltipLines.map((line, i) => (
              // eslint-disable-next-line react/no-array-index-key
              <div key={i}>{line}</div>
            ))}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// ---------------------------------------------------------------------------
// Inline limit editor dialog
// ---------------------------------------------------------------------------

interface LimitEditorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  entry: ModelCatalogEntry;
  window: UsageWindow;
}

function LimitEditorDialog({ open, onOpenChange, entry, window: usageWindow }: LimitEditorDialogProps) {
  const setLimitsMutation = useSetModelTokenLimits();

  const current24h = entry.limit_24h;
  const current30d = entry.limit_30d;
  const currentValue = usageWindow === "24h" ? current24h : current30d;

  const [raw, setRaw] = useState(currentValue != null ? String(currentValue) : "");
  const [error, setError] = useState<string | null>(null);

  const windowLabel = usageWindow === "24h" ? "24-hour" : "30-day";

  function handleSave() {
    const trimmed = raw.trim();
    let newLimit: number | null;
    if (trimmed === "" || trimmed === "-") {
      newLimit = null;
    } else {
      const parsed = parseInt(trimmed, 10);
      if (isNaN(parsed) || parsed < 1) {
        setError("Enter a positive integer or leave blank to remove the limit");
        return;
      }
      newLimit = parsed;
    }

    const body = usageWindow === "24h"
      ? { limit_24h: newLimit, limit_30d: current30d ?? null }
      : { limit_24h: current24h ?? null, limit_30d: newLimit };

    setLimitsMutation.mutate(
      { id: entry.id, body },
      {
        onSuccess: () => {
          toast.success(
            `${windowLabel} limit ${newLimit != null
              ? `set to ${formatTokensExact(newLimit)} tokens`
              : "removed"} for ${entry.alias}`,
          );
          onOpenChange(false);
        },
        onError: (err) => {
          setError(err instanceof Error ? err.message : "Failed to save limit");
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Edit {windowLabel} token limit</DialogTitle>
          <DialogDescription>
            Set a rolling token budget for <strong>{entry.alias}</strong>.
            Leave blank to remove the limit (unlimited).
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="limit-value">Token limit</Label>
            <Input
              id="limit-value"
              type="number"
              min={1}
              value={raw}
              onChange={(e) => { setRaw(e.target.value); setError(null); }}
              placeholder="e.g. 500000 (blank = unlimited)"
              disabled={setLimitsMutation.isPending}
            />
            <p className="text-xs text-muted-foreground">
              Total tokens (input + output) per rolling {usageWindow} window.
            </p>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={setLimitsMutation.isPending}
          >
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={setLimitsMutation.isPending}>
            {setLimitsMutation.isPending ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Preset templates
// ---------------------------------------------------------------------------

interface ModelPreset {
  label: string;
  values: Partial<ModelFormValues>;
}

const MODEL_PRESETS: ModelPreset[] = [
  {
    label: "Codex o3 (High reasoning)",
    values: {
      runtime_type: "codex",
      model_id: "o3",
      extra_args_raw: JSON.stringify(["--reasoning-effort", "high"], null, 2),
    },
  },
  {
    label: "Codex o3 (Low reasoning)",
    values: {
      runtime_type: "codex",
      model_id: "o3",
      extra_args_raw: JSON.stringify(["--reasoning-effort", "low"], null, 2),
    },
  },
  {
    label: "Claude 3.7 Sonnet",
    values: {
      runtime_type: "claude",
      model_id: "claude-sonnet-4-5",
      extra_args_raw: "[]",
    },
  },
  {
    label: "Claude 3.7 Sonnet (extended thinking)",
    values: {
      runtime_type: "claude",
      model_id: "claude-sonnet-4-5",
      extra_args_raw: JSON.stringify(["--thinking", "extended"], null, 2),
    },
  },
];

// ---------------------------------------------------------------------------
// Form types
// ---------------------------------------------------------------------------

interface ModelFormValues {
  alias: string;
  runtime_type: string;
  model_id: string;
  extra_args_raw: string;
  complexity_tier: ComplexityTier;
  priority: string;
  enabled: boolean;
}

function defaultFormValues(entry?: ModelCatalogEntry | null): ModelFormValues {
  return {
    alias: entry?.alias ?? "",
    runtime_type: entry?.runtime_type ?? "claude",
    model_id: entry?.model_id ?? "",
    extra_args_raw: entry ? JSON.stringify(entry.extra_args, null, 2) : "[]",
    complexity_tier: entry?.complexity_tier ?? "medium",
    priority: String(entry?.priority ?? 0),
    enabled: entry?.enabled ?? true,
  };
}

function parseExtraArgs(raw: string): { value: string[]; error: string | null } {
  const trimmed = raw.trim();
  if (!trimmed || trimmed === "[]") return { value: [], error: null };
  try {
    const parsed = JSON.parse(trimmed);
    if (!Array.isArray(parsed)) return { error: "Extra args must be a JSON array", value: [] };
    return { value: parsed.map(String), error: null };
  } catch {
    return { error: "Extra args must be valid JSON array", value: [] };
  }
}

// ---------------------------------------------------------------------------
// Model form dialog
// ---------------------------------------------------------------------------

interface ModelFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  entry?: ModelCatalogEntry | null;
  onSubmit: (values: ModelCatalogCreate) => void;
  isSubmitting: boolean;
  error?: string | null;
}

function ModelFormDialog({
  open,
  onOpenChange,
  entry,
  onSubmit,
  isSubmitting,
  error,
}: ModelFormDialogProps) {
  const isEdit = !!entry;
  const formKey = open ? (entry?.id ?? "new") : "closed";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit Model" : "Add Model"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update the model catalog entry."
              : "Add a new model to the catalog."}
          </DialogDescription>
        </DialogHeader>
        <ModelFormFields
          key={formKey}
          entry={entry}
          onSubmit={onSubmit}
          onCancel={() => onOpenChange(false)}
          isSubmitting={isSubmitting}
          error={error ?? null}
        />
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Inner form fields (remounted via key)
// ---------------------------------------------------------------------------

function ModelFormFields({
  entry,
  onSubmit,
  onCancel,
  isSubmitting,
  error,
}: {
  entry?: ModelCatalogEntry | null;
  onSubmit: (values: ModelCatalogCreate) => void;
  onCancel: () => void;
  isSubmitting: boolean;
  error: string | null;
}) {
  const isEdit = !!entry;
  const [values, setValues] = useState<ModelFormValues>(defaultFormValues(entry));

  function applyPreset(preset: ModelPreset) {
    setValues((prev) => ({ ...prev, ...preset.values }));
  }

  const parsedArgs = parseExtraArgs(values.extra_args_raw);
  const priorityNum = parseInt(values.priority, 10);
  const priorityValid = !isNaN(priorityNum);
  const isValid =
    values.alias.trim() !== "" &&
    values.runtime_type.trim() !== "" &&
    values.model_id.trim() !== "" &&
    parsedArgs.error === null &&
    priorityValid;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!isValid || isSubmitting) return;
    onSubmit({
      alias: values.alias.trim(),
      runtime_type: values.runtime_type.trim(),
      model_id: values.model_id.trim(),
      extra_args: parsedArgs.value,
      complexity_tier: values.complexity_tier,
      priority: priorityNum,
      enabled: values.enabled,
    });
  }

  const displayError = parsedArgs.error ?? (!priorityValid ? "Priority must be an integer" : null) ?? error;

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {/* Preset selector */}
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">Use template</Label>
        <Select
          value=""
          onValueChange={(v) => {
            const preset = MODEL_PRESETS.find((p) => p.label === v);
            if (preset) applyPreset(preset);
          }}
        >
          <SelectTrigger>
            <SelectValue placeholder="Select a preset..." />
          </SelectTrigger>
          <SelectContent>
            {MODEL_PRESETS.map((p) => (
              <SelectItem key={p.label} value={p.label}>
                {p.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="border-t border-border" />

      <div className="space-y-2">
        <Label htmlFor="model-alias">Alias</Label>
        <Input
          id="model-alias"
          value={values.alias}
          onChange={(e) => setValues((v) => ({ ...v, alias: e.target.value }))}
          placeholder="e.g. claude-default"
          disabled={isSubmitting}
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label htmlFor="model-runtime-type">Runtime Type</Label>
          <Select
            value={values.runtime_type}
            onValueChange={(v) => setValues((prev) => ({ ...prev, runtime_type: v }))}
            disabled={isSubmitting}
          >
            <SelectTrigger id="model-runtime-type">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="claude">claude</SelectItem>
              <SelectItem value="codex">codex</SelectItem>
              <SelectItem value="opencode">opencode</SelectItem>
              <SelectItem value="gemini">gemini</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="model-id">Model ID</Label>
          <Input
            id="model-id"
            value={values.model_id}
            onChange={(e) => setValues((v) => ({ ...v, model_id: e.target.value }))}
            placeholder="e.g. claude-sonnet-4-5"
            disabled={isSubmitting}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="model-extra-args">Extra Args (JSON array)</Label>
        <Textarea
          id="model-extra-args"
          value={values.extra_args_raw}
          onChange={(e) => setValues((v) => ({ ...v, extra_args_raw: e.target.value }))}
          placeholder='e.g. ["--reasoning-effort", "high"]'
          className="min-h-16 font-mono text-xs"
          disabled={isSubmitting}
        />
        <p className="text-xs text-muted-foreground">
          JSON array of CLI arguments passed to the runtime.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label htmlFor="model-complexity-tier">Complexity Tier</Label>
          <Select
            value={values.complexity_tier}
            onValueChange={(v) =>
              setValues((prev) => ({ ...prev, complexity_tier: v as ComplexityTier }))
            }
            disabled={isSubmitting}
          >
            <SelectTrigger id="model-complexity-tier">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {COMPLEXITY_TIERS.map((tier) => (
                <SelectItem key={tier} value={tier}>
                  {complexityLabel(tier)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="model-priority">Priority</Label>
          <Input
            id="model-priority"
            type="number"
            value={values.priority}
            onChange={(e) => setValues((v) => ({ ...v, priority: e.target.value }))}
            placeholder="0"
            disabled={isSubmitting}
          />
          <p className="text-xs text-muted-foreground">Higher = higher priority.</p>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <input
          id="model-enabled"
          type="checkbox"
          checked={values.enabled}
          onChange={(e) => setValues((v) => ({ ...v, enabled: e.target.checked }))}
          className="h-4 w-4 rounded border-input"
          disabled={isSubmitting}
        />
        <Label htmlFor="model-enabled" className="text-sm">
          Enabled
        </Label>
      </div>

      {displayError && (
        <p className="text-sm text-destructive">{displayError}</p>
      )}

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" disabled={!isValid || isSubmitting}>
          {isSubmitting
            ? isEdit
              ? "Updating..."
              : "Creating..."
            : isEdit
              ? "Update Model"
              : "Add Model"}
        </Button>
      </DialogFooter>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Delete confirmation dialog
// ---------------------------------------------------------------------------

function DeleteModelDialog({
  entry,
  open,
  onOpenChange,
  onConfirm,
  isDeleting,
}: {
  entry: ModelCatalogEntry | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  isDeleting: boolean;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete Model?</DialogTitle>
          <DialogDescription>
            Are you sure you want to delete <strong>{entry?.alias}</strong>?{" "}
            This will also remove all per-butler overrides for this model.
            This action cannot be undone.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isDeleting}
          >
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={isDeleting}>
            {isDeleting ? "Deleting..." : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Skeleton rows
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 4 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-28" /></TableCell>
          <TableCell><Skeleton className="h-4 w-16" /></TableCell>
          <TableCell><Skeleton className="h-4 w-40" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-16" /></TableCell>
          <TableCell><Skeleton className="h-4 w-12" /></TableCell>
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
          <TableCell className="text-right"><Skeleton className="h-4 w-16 ml-auto" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// ModelCatalogCard
// ---------------------------------------------------------------------------

export function ModelCatalogCard() {
  const { data, isLoading, isError } = useModelCatalog();
  const entries = data?.data ?? [];

  const createMutation = useCreateModelCatalogEntry();
  const updateMutation = useUpdateModelCatalogEntry();
  const deleteMutation = useDeleteModelCatalogEntry();
  const testMutation = useTestModelCatalogEntry();

  const [formOpen, setFormOpen] = useState(false);
  const [editingEntry, setEditingEntry] = useState<ModelCatalogEntry | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ModelCatalogEntry | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [testingIds, setTestingIds] = useState<Set<string>>(new Set());
  const [testResults, setTestResults] = useState<Record<string, ModelTestResult>>({});

  // Limit editor: which entry + window is being edited
  const [limitEditor, setLimitEditor] = useState<{
    entry: ModelCatalogEntry;
    window: UsageWindow;
  } | null>(null);

  function handleAddClick() {
    setEditingEntry(null);
    setFormOpen(true);
  }

  function handleEditClick(entry: ModelCatalogEntry) {
    setEditingEntry(entry);
    setFormOpen(true);
  }

  function handleDeleteClick(entry: ModelCatalogEntry) {
    setDeleteTarget(entry);
    setDeleteDialogOpen(true);
  }

  function handleTestClick(entry: ModelCatalogEntry) {
    setTestingIds((prev) => new Set(prev).add(entry.id));
    setTestResults((prev) => {
      const next = { ...prev };
      delete next[entry.id];
      return next;
    });
    testMutation.mutate(entry.id, {
      onSuccess: (resp) => {
        setTestResults((prev) => ({ ...prev, [entry.id]: resp.data }));
        if (resp.data.success) {
          toast.success(
            `Sent 'Reply with exactly: OK'; ${entry.alias} responded with '${resp.data.reply}' in ${resp.data.duration_ms}ms`,
          );
        } else {
          toast.error(`${entry.alias} failed: ${resp.data.error}`);
        }
      },
      onError: (err) => {
        setTestResults((prev) => ({
          ...prev,
          [entry.id]: {
            success: false,
            reply: null,
            error: err instanceof Error ? err.message : "Unknown error",
            duration_ms: 0,
          },
        }));
        toast.error(`Test failed: ${err instanceof Error ? err.message : "Unknown error"}`);
      },
      onSettled: () => {
        setTestingIds((prev) => {
          const next = new Set(prev);
          next.delete(entry.id);
          return next;
        });
      },
    });
  }

  function handleToggleEnabled(entry: ModelCatalogEntry) {
    updateMutation.mutate(
      { id: entry.id, body: { enabled: !entry.enabled } },
      {
        onSuccess: () =>
          toast.success(
            `Model "${entry.alias}" ${entry.enabled ? "disabled" : "enabled"}`,
          ),
        onError: (err) =>
          toast.error(
            `Failed: ${err instanceof Error ? err.message : "Unknown error"}`,
          ),
      },
    );
  }

  function handleFormSubmit(values: ModelCatalogCreate) {
    if (editingEntry) {
      updateMutation.mutate(
        { id: editingEntry.id, body: values },
        {
          onSuccess: () => {
            toast.success(`Model "${values.alias}" updated`);
            setFormOpen(false);
            setEditingEntry(null);
          },
          onError: (err) =>
            toast.error(
              `Failed to update: ${err instanceof Error ? err.message : "Unknown error"}`,
            ),
        },
      );
    } else {
      createMutation.mutate(values, {
        onSuccess: () => {
          toast.success(`Model "${values.alias}" created`);
          setFormOpen(false);
        },
        onError: (err) =>
          toast.error(
            `Failed to create: ${err instanceof Error ? err.message : "Unknown error"}`,
          ),
      });
    }
  }

  function handleDeleteConfirm() {
    if (!deleteTarget) return;
    deleteMutation.mutate(deleteTarget.id, {
      onSuccess: () => {
        toast.success(`Model "${deleteTarget.alias}" deleted`);
        setDeleteDialogOpen(false);
        setDeleteTarget(null);
      },
      onError: (err) =>
        toast.error(
          `Failed to delete: ${err instanceof Error ? err.message : "Unknown error"}`,
        ),
    });
  }

  // Group entries by complexity tier for display
  const grouped = COMPLEXITY_TIERS.map((tier) => ({
    tier,
    entries: entries.filter((e) => e.complexity_tier === tier),
  })).filter((g) => g.entries.length > 0);

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle>Model Catalog</CardTitle>
            <CardDescription>
              Manage the shared model catalog. Each entry maps an alias to a
              runtime type, model ID, and complexity tier.
            </CardDescription>
          </div>
          <Button size="sm" onClick={handleAddClick}>
            Add Model
          </Button>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Alias</TableHead>
                  <TableHead>Runtime</TableHead>
                  <TableHead>Model ID</TableHead>
                  <TableHead>Extra Args</TableHead>
                  <TableHead>Priority</TableHead>
                  <TableHead>Enabled</TableHead>
                  <TableHead>24h</TableHead>
                  <TableHead>30d</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <SkeletonRows />
              </TableBody>
            </Table>
          ) : isError ? (
            <p className="text-sm text-destructive">
              Failed to load model catalog. Ensure the dashboard API is running.
            </p>
          ) : entries.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No models in the catalog yet. Add one to get started.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Alias</TableHead>
                  <TableHead>Runtime</TableHead>
                  <TableHead>Model ID</TableHead>
                  <TableHead>Extra Args</TableHead>
                  <TableHead>Priority</TableHead>
                  <TableHead>Enabled</TableHead>
                  <TableHead
                    className="text-xs"
                    title="Rolling 24h token usage. Click the limit to edit."
                  >
                    24h
                  </TableHead>
                  <TableHead
                    className="text-xs"
                    title="Rolling 30d token usage. Click the limit to edit."
                  >
                    30d
                  </TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {grouped.map(({ tier, entries: tierEntries }) => (
                  <>
                    <TableRow key={`tier-${tier}`} className="hover:bg-transparent">
                      <TableCell colSpan={9} className="py-2 px-0">
                        <div className="flex items-center gap-2">
                          <ComplexityBadge tier={tier} />
                          {tier === "discretion" && (
                            <TooltipProvider>
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Info className="h-3.5 w-3.5 text-muted-foreground cursor-help" />
                                </TooltipTrigger>
                                <TooltipContent side="right" className="max-w-64">
                                  High-volume, high-noise traffic (e.g. discretion gates) —
                                  best served by local models to avoid exorbitant cloud costs.
                                </TooltipContent>
                              </Tooltip>
                            </TooltipProvider>
                          )}
                          <span className="text-xs text-muted-foreground">
                            {tierEntries.length} model{tierEntries.length !== 1 ? "s" : ""}
                          </span>
                        </div>
                      </TableCell>
                    </TableRow>
                    {tierEntries.map((entry) => (
                      <TableRow key={entry.id}>
                        <TableCell className="font-medium">
                          {entry.alias}
                        </TableCell>
                        <TableCell>
                          <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                            {entry.runtime_type}
                          </code>
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">
                          {entry.model_id}
                        </TableCell>
                        <TableCell className="max-w-xs">
                          {entry.extra_args.length > 0 ? (
                            <code className="text-xs text-muted-foreground truncate block">
                              {entry.extra_args.join(" ")}
                            </code>
                          ) : (
                            <span className="text-xs text-muted-foreground">&mdash;</span>
                          )}
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1">
                            <span className="text-xs text-muted-foreground tabular-nums w-6 text-right">
                              {entry.priority}
                            </span>
                            <div className="flex flex-col">
                              <button
                                type="button"
                                className="p-0 h-3.5 w-3.5 flex items-center justify-center rounded hover:bg-muted text-muted-foreground hover:text-foreground disabled:opacity-30"
                                title="Increase priority (+5)"
                                disabled={updateMutation.isPending}
                                onClick={() =>
                                  updateMutation.mutate(
                                    { id: entry.id, body: { priority: entry.priority + 5 } },
                                    {
                                      onError: (err) =>
                                        toast.error(`Failed: ${err instanceof Error ? err.message : "Unknown error"}`),
                                    },
                                  )
                                }
                              >
                                <ChevronUp className="h-3 w-3" />
                              </button>
                              <button
                                type="button"
                                className="p-0 h-3.5 w-3.5 flex items-center justify-center rounded hover:bg-muted text-muted-foreground hover:text-foreground disabled:opacity-30"
                                title="Decrease priority (-5)"
                                disabled={updateMutation.isPending}
                                onClick={() =>
                                  updateMutation.mutate(
                                    { id: entry.id, body: { priority: entry.priority - 5 } },
                                    {
                                      onError: (err) =>
                                        toast.error(`Failed: ${err instanceof Error ? err.message : "Unknown error"}`),
                                    },
                                  )
                                }
                              >
                                <ChevronDown className="h-3 w-3" />
                              </button>
                            </div>
                          </div>
                        </TableCell>
                        <TableCell>
                          <button
                            type="button"
                            onClick={() => handleToggleEnabled(entry)}
                            className="cursor-pointer"
                            title={
                              entry.enabled ? "Click to disable" : "Click to enable"
                            }
                            disabled={updateMutation.isPending}
                          >
                            {entry.enabled ? (
                              <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">
                                On
                              </Badge>
                            ) : (
                              <Badge variant="secondary">Off</Badge>
                            )}
                          </button>
                        </TableCell>
                        {/* 24h usage column */}
                        <TableCell className="min-w-[120px]">
                          <UsageBar
                            entryId={entry.id}
                            window="24h"
                            used={entry.usage_24h}
                            limit={entry.limit_24h}
                            onLimitClick={() => setLimitEditor({ entry, window: "24h" })}
                          />
                        </TableCell>
                        {/* 30d usage column */}
                        <TableCell className="min-w-[120px]">
                          <UsageBar
                            entryId={entry.id}
                            window="30d"
                            used={entry.usage_30d}
                            limit={entry.limit_30d}
                            onLimitClick={() => setLimitEditor({ entry, window: "30d" })}
                          />
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleTestClick(entry)}
                              disabled={testingIds.has(entry.id)}
                              title={
                                testResults[entry.id]
                                  ? testResults[entry.id].success
                                    ? `OK (${testResults[entry.id].duration_ms}ms): ${testResults[entry.id].reply}`
                                    : `Failed: ${testResults[entry.id].error}`
                                  : "Send a test message to verify this model works"
                              }
                            >
                              {testingIds.has(entry.id) ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : testResults[entry.id] ? (
                                testResults[entry.id].success ? (
                                  <Check className="h-3.5 w-3.5 text-emerald-600" />
                                ) : (
                                  <X className="h-3.5 w-3.5 text-destructive" />
                                )
                              ) : (
                                <FlaskConical className="h-3.5 w-3.5" />
                              )}
                              Test
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleEditClick(entry)}
                            >
                              Edit
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              className="text-destructive hover:bg-destructive/10"
                              onClick={() => handleDeleteClick(entry)}
                            >
                              Delete
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Create / Edit dialog */}
      <ModelFormDialog
        open={formOpen}
        onOpenChange={(open) => {
          setFormOpen(open);
          if (!open) setEditingEntry(null);
        }}
        entry={editingEntry}
        onSubmit={handleFormSubmit}
        isSubmitting={createMutation.isPending || updateMutation.isPending}
        error={
          createMutation.error instanceof Error
            ? createMutation.error.message
            : updateMutation.error instanceof Error
              ? updateMutation.error.message
              : null
        }
      />

      {/* Delete confirmation */}
      <DeleteModelDialog
        entry={deleteTarget}
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        onConfirm={handleDeleteConfirm}
        isDeleting={deleteMutation.isPending}
      />

      {/* Limit editor dialog */}
      {limitEditor && (
        <LimitEditorDialog
          open={!!limitEditor}
          onOpenChange={(open) => { if (!open) setLimitEditor(null); }}
          entry={limitEditor.entry}
          window={limitEditor.window}
        />
      )}
    </>
  );
}

export default ModelCatalogCard;
