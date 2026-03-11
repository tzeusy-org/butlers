import { useState } from "react";
import { toast } from "sonner";

import type { ComplexityTier, ModelCatalogCreate, ModelCatalogEntry } from "@/api/types.ts";
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
  useCreateModelCatalogEntry,
  useDeleteModelCatalogEntry,
  useModelCatalog,
  useUpdateModelCatalogEntry,
} from "@/hooks/use-model-catalog.ts";

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
          <p className="text-xs text-muted-foreground">Lower = higher priority.</p>
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

  const [formOpen, setFormOpen] = useState(false);
  const [editingEntry, setEditingEntry] = useState<ModelCatalogEntry | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ModelCatalogEntry | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

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
                  <TableHead>Complexity</TableHead>
                  <TableHead>Priority</TableHead>
                  <TableHead>Enabled</TableHead>
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
            <div className="space-y-4">
              {grouped.map(({ tier, entries: tierEntries }) => (
                <div key={tier}>
                  <div className="mb-2 flex items-center gap-2">
                    <ComplexityBadge tier={tier} />
                    <span className="text-xs text-muted-foreground">
                      {tierEntries.length} model{tierEntries.length !== 1 ? "s" : ""}
                    </span>
                  </div>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Alias</TableHead>
                        <TableHead>Runtime</TableHead>
                        <TableHead>Model ID</TableHead>
                        <TableHead>Extra Args</TableHead>
                        <TableHead>Priority</TableHead>
                        <TableHead>Enabled</TableHead>
                        <TableHead className="text-right">Actions</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
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
                          <TableCell className="text-xs text-muted-foreground">
                            {entry.priority}
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
                          <TableCell className="text-right">
                            <div className="flex justify-end gap-1">
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
                    </TableBody>
                  </Table>
                </div>
              ))}
            </div>
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
    </>
  );
}

export default ModelCatalogCard;
