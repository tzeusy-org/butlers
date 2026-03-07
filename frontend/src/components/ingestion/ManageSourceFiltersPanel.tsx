/**
 * ManageSourceFiltersPanel — right-side Sheet panel for CRUD management of
 * named source filter objects.
 *
 * Opens from the FiltersTab via a "Manage Filters" link. Provides:
 *   - Table listing all named filters (Name / Mode / Pattern Type / Count / Actions)
 *   - Inline create form with full validation
 *   - Inline edit form (pre-filled, same fields)
 *   - Delete with confirmation dialog
 */

import { useEffect, useState } from "react";
import { Edit2, Loader2, Plus, Trash2, X } from "lucide-react";

import type { SourceFilter, SourceFilterCreate, SourceFilterMode } from "@/api/index.ts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useCreateSourceFilter,
  useDeleteSourceFilter,
  useSourceFilters,
  useUpdateSourceFilter,
} from "@/hooks/use-source-filters";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const FILTER_MODES: { value: SourceFilterMode; label: string }[] = [
  { value: "blacklist", label: "Blacklist" },
  { value: "whitelist", label: "Whitelist" },
];

const SOURCE_KEY_TYPES = [
  { value: "domain", label: "Domain" },
  { value: "sender_address", label: "Sender address" },
  { value: "substring", label: "Substring" },
];

// ---------------------------------------------------------------------------
// PatternTagInput — chip/pill input for pattern lists
// ---------------------------------------------------------------------------

interface PatternTagInputProps {
  patterns: string[];
  onAdd: (p: string) => void;
  onRemove: (p: string) => void;
  testId?: string;
}

function PatternTagInput({ patterns, onAdd, onRemove, testId }: PatternTagInputProps) {
  const [value, setValue] = useState("");

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if ((e.key === "Enter" || e.key === ",") && value.trim()) {
      e.preventDefault();
      onAdd(value.trim());
      setValue("");
    }
    if (e.key === "Backspace" && !value && patterns.length > 0) {
      onRemove(patterns[patterns.length - 1]);
    }
  }

  return (
    <div
      className="flex flex-wrap items-center gap-1.5 rounded-md border border-input bg-background p-2 min-h-[2.5rem] focus-within:ring-1 focus-within:ring-ring"
      data-testid={testId}
    >
      {patterns.map((p) => (
        <Badge key={p} variant="secondary" className="gap-1 py-0.5">
          {p}
          <button
            type="button"
            className="ml-0.5 rounded-sm opacity-60 hover:opacity-100"
            onClick={() => onRemove(p)}
            aria-label={`Remove pattern ${p}`}
          >
            <X className="size-3" />
          </button>
        </Badge>
      ))}
      <input
        className="flex-1 min-w-[8rem] bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        placeholder={patterns.length === 0 ? "Type a pattern and press Enter…" : "Add pattern…"}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        data-testid={testId ? `${testId}-input` : undefined}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// FilterForm — shared create/edit form
// ---------------------------------------------------------------------------

interface FilterFormState {
  name: string;
  description: string;
  filter_mode: SourceFilterMode;
  source_key_type: string;
  patterns: string[];
}

function emptyForm(): FilterFormState {
  return {
    name: "",
    description: "",
    filter_mode: "blacklist",
    source_key_type: "domain",
    patterns: [],
  };
}

function formFromFilter(sf: SourceFilter): FilterFormState {
  return {
    name: sf.name,
    description: sf.description ?? "",
    filter_mode: sf.filter_mode,
    source_key_type: sf.source_key_type,
    patterns: sf.patterns,
  };
}

interface FilterFormProps {
  form: FilterFormState;
  onChange: (f: FilterFormState) => void;
  /** If set, these fields are read-only (mode + type cannot change after creation). */
  readOnlyModeType?: boolean;
  error: string | null;
  testIdPrefix?: string;
}

function FilterForm({ form, onChange, readOnlyModeType, error, testIdPrefix }: FilterFormProps) {
  const p = testIdPrefix ?? "filter-form";

  function addPattern(pattern: string) {
    if (!form.patterns.includes(pattern)) {
      onChange({ ...form, patterns: [...form.patterns, pattern] });
    }
  }
  function removePattern(pattern: string) {
    onChange({ ...form, patterns: form.patterns.filter((x) => x !== pattern) });
  }

  return (
    <div className="space-y-4">
      {error && (
        <p className="text-sm text-destructive" data-testid={`${p}-error`}>
          {error}
        </p>
      )}

      {/* Name */}
      <div className="space-y-1">
        <Label htmlFor={`${p}-name`}>Name</Label>
        <Input
          id={`${p}-name`}
          value={form.name}
          onChange={(e) => onChange({ ...form, name: e.target.value })}
          placeholder="e.g. Block marketing senders"
          data-testid={`${p}-name`}
        />
      </div>

      {/* Description */}
      <div className="space-y-1">
        <Label htmlFor={`${p}-description`}>
          Description <span className="text-muted-foreground font-normal">(optional)</span>
        </Label>
        <Input
          id={`${p}-description`}
          value={form.description}
          onChange={(e) => onChange({ ...form, description: e.target.value })}
          placeholder="Short explanation of what this filter does"
          data-testid={`${p}-description`}
        />
      </div>

      {/* Mode */}
      <div className="space-y-1">
        <Label htmlFor={`${p}-mode`}>Mode</Label>
        {readOnlyModeType ? (
          <p className="text-sm font-medium">
            {FILTER_MODES.find((m) => m.value === form.filter_mode)?.label ?? form.filter_mode}
          </p>
        ) : (
          <select
            id={`${p}-mode`}
            className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
            value={form.filter_mode}
            onChange={(e) =>
              onChange({ ...form, filter_mode: e.target.value as SourceFilterMode })
            }
            data-testid={`${p}-mode`}
          >
            {FILTER_MODES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Pattern type */}
      <div className="space-y-1">
        <Label htmlFor={`${p}-type`}>Pattern type</Label>
        {readOnlyModeType ? (
          <p className="text-sm font-medium">
            {SOURCE_KEY_TYPES.find((t) => t.value === form.source_key_type)?.label ??
              form.source_key_type}
          </p>
        ) : (
          <select
            id={`${p}-type`}
            className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
            value={form.source_key_type}
            onChange={(e) => onChange({ ...form, source_key_type: e.target.value })}
            data-testid={`${p}-type`}
          >
            {SOURCE_KEY_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Patterns */}
      <div className="space-y-1">
        <Label>Patterns</Label>
        <PatternTagInput
          patterns={form.patterns}
          onAdd={addPattern}
          onRemove={removePattern}
          testId={`${p}-patterns`}
        />
        <p className="text-xs text-muted-foreground">
          Press Enter or comma after each pattern.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CreateFilterForm — inline form shown below the table
// ---------------------------------------------------------------------------

interface CreateFilterFormProps {
  onCreated: () => void;
  onCancel: () => void;
}

function CreateFilterForm({ onCreated, onCancel }: CreateFilterFormProps) {
  const [form, setForm] = useState<FilterFormState>(emptyForm);
  const [error, setError] = useState<string | null>(null);
  const createMutation = useCreateSourceFilter();

  async function handleSubmit() {
    setError(null);
    if (!form.name.trim()) {
      setError("Name is required.");
      return;
    }
    if (form.patterns.length === 0) {
      setError("At least one pattern is required.");
      return;
    }

    const body: SourceFilterCreate = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      filter_mode: form.filter_mode,
      source_key_type: form.source_key_type,
      patterns: form.patterns,
    };

    try {
      await createMutation.mutateAsync(body);
      onCreated();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to create filter.";
      if (msg.includes("already exists") || msg.includes("409")) {
        setError(`A filter named "${form.name}" already exists. Choose a unique name.`);
      } else {
        setError(msg);
      }
    }
  }

  return (
    <div className="border rounded-lg p-4 space-y-4 bg-muted/30" data-testid="create-filter-form">
      <h4 className="text-sm font-semibold">Create filter</h4>
      <FilterForm
        form={form}
        onChange={setForm}
        error={error}
        testIdPrefix="create-filter"
      />
      <div className="flex gap-2">
        <Button
          size="sm"
          onClick={handleSubmit}
          disabled={createMutation.isPending}
          data-testid="create-filter-submit"
        >
          {createMutation.isPending && <Loader2 className="size-3 mr-1 animate-spin" />}
          Create filter
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel} data-testid="create-filter-cancel">
          Cancel
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// EditFilterForm — inline edit form for an existing filter
// ---------------------------------------------------------------------------

interface EditFilterFormProps {
  filter: SourceFilter;
  onSaved: () => void;
  onCancel: () => void;
}

function EditFilterForm({ filter, onSaved, onCancel }: EditFilterFormProps) {
  const [form, setForm] = useState<FilterFormState>(() => formFromFilter(filter));
  const [error, setError] = useState<string | null>(null);
  const updateMutation = useUpdateSourceFilter();

  async function handleSubmit() {
    setError(null);
    if (!form.name.trim()) {
      setError("Name is required.");
      return;
    }
    if (form.patterns.length === 0) {
      setError("At least one pattern is required.");
      return;
    }

    try {
      await updateMutation.mutateAsync({
        id: filter.id,
        body: {
          name: form.name.trim(),
          description: form.description.trim() || null,
          patterns: form.patterns,
        },
      });
      onSaved();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update filter.";
      if (msg.includes("already exists") || msg.includes("409")) {
        setError(`A filter named "${form.name}" already exists. Choose a unique name.`);
      } else {
        setError(msg);
      }
    }
  }

  return (
    <div className="border rounded-lg p-4 space-y-4 bg-muted/30" data-testid={`edit-filter-form-${filter.id}`}>
      <h4 className="text-sm font-semibold">Edit filter</h4>
      <FilterForm
        form={form}
        onChange={setForm}
        readOnlyModeType
        error={error}
        testIdPrefix="edit-filter"
      />
      <div className="flex gap-2">
        <Button
          size="sm"
          onClick={handleSubmit}
          disabled={updateMutation.isPending}
          data-testid="edit-filter-submit"
        >
          {updateMutation.isPending && <Loader2 className="size-3 mr-1 animate-spin" />}
          Save changes
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel} data-testid="edit-filter-cancel">
          Cancel
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DeleteConfirmDialog
// ---------------------------------------------------------------------------

interface DeleteConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filter: SourceFilter | null;
  onConfirm: (id: string) => Promise<void>;
}

function DeleteConfirmDialog({
  open,
  onOpenChange,
  filter,
  onConfirm,
}: DeleteConfirmDialogProps) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setPending(false);
      setError(null);
    }
  }, [open]);

  async function handleConfirm() {
    if (!filter) return;
    setPending(true);
    setError(null);
    try {
      await onConfirm(filter.id);
      onOpenChange(false);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to delete filter.");
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="delete-filter-dialog">
        <DialogHeader>
          <DialogTitle>Delete source filter</DialogTitle>
          <DialogDescription>
            Are you sure you want to delete{" "}
            <strong>{filter?.name}</strong>? This action cannot be undone.
            Any connector assignments to this filter will also be removed.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <p className="text-sm text-destructive" data-testid="delete-filter-error">
            {error}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleConfirm}
            disabled={pending}
            data-testid="delete-filter-confirm"
          >
            {pending && <Loader2 className="size-3 mr-1 animate-spin" />}
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// FiltersTable — table of all source filters
// ---------------------------------------------------------------------------

interface FiltersTableProps {
  filters: SourceFilter[];
  onEdit: (f: SourceFilter) => void;
  onDelete: (f: SourceFilter) => void;
  editingId: string | null;
}

function FiltersTable({ filters, onEdit, onDelete, editingId }: FiltersTableProps) {
  if (filters.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-4" data-testid="filters-empty">
        No source filters yet. Create one below.
      </p>
    );
  }

  return (
    <Table data-testid="source-filters-table">
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Mode</TableHead>
          <TableHead>Pattern type</TableHead>
          <TableHead className="text-right">Patterns</TableHead>
          <TableHead className="w-[100px]">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {filters.map((sf) => (
          <TableRow
            key={sf.id}
            data-testid={`filter-row-${sf.id}`}
            data-state={editingId === sf.id ? "editing" : undefined}
          >
            <TableCell className="font-medium">{sf.name}</TableCell>
            <TableCell>
              <Badge variant={sf.filter_mode === "blacklist" ? "destructive" : "default"}>
                {sf.filter_mode}
              </Badge>
            </TableCell>
            <TableCell className="text-sm text-muted-foreground">{sf.source_key_type}</TableCell>
            <TableCell className="text-right text-sm">{sf.patterns.length}</TableCell>
            <TableCell>
              <div className="flex items-center gap-1">
                <Button
                  size="icon"
                  variant="ghost"
                  className="size-7"
                  onClick={() => onEdit(sf)}
                  aria-label={`Edit filter ${sf.name}`}
                  data-testid={`edit-filter-${sf.id}`}
                >
                  <Edit2 className="size-3.5" />
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  className="size-7 text-destructive hover:text-destructive"
                  onClick={() => onDelete(sf)}
                  aria-label={`Delete filter ${sf.name}`}
                  data-testid={`delete-filter-${sf.id}`}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------------------
// ManageSourceFiltersPanel — main export
// ---------------------------------------------------------------------------

interface ManageSourceFiltersPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ManageSourceFiltersPanel({
  open,
  onOpenChange,
}: ManageSourceFiltersPanelProps) {
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingFilter, setEditingFilter] = useState<SourceFilter | null>(null);
  const [deleteFilter, setDeleteFilter] = useState<SourceFilter | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const { data, isLoading, error } = useSourceFilters({ enabled: open });
  const deleteMutation = useDeleteSourceFilter();

  const filters = data?.data ?? [];

  function handleEdit(sf: SourceFilter) {
    setShowCreateForm(false);
    setEditingFilter(sf);
  }

  function handleDelete(sf: SourceFilter) {
    setDeleteFilter(sf);
    setDeleteOpen(true);
  }

  function handleEditSaved() {
    setEditingFilter(null);
  }

  function handleEditCancel() {
    setEditingFilter(null);
  }

  function handleCreateCreated() {
    setShowCreateForm(false);
  }

  function handleCreateCancel() {
    setShowCreateForm(false);
  }

  async function handleDeleteConfirm(id: string) {
    await deleteMutation.mutateAsync(id);
    if (editingFilter?.id === id) {
      setEditingFilter(null);
    }
  }

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent
          side="right"
          className="w-full sm:max-w-2xl overflow-y-auto"
          data-testid="manage-source-filters-panel"
        >
          <SheetHeader>
            <SheetTitle>Manage Source Filters</SheetTitle>
            <SheetDescription>
              Named source filters define reusable blacklists and whitelists that can be
              assigned to one or more connectors.
            </SheetDescription>
          </SheetHeader>

          <div className="space-y-6 py-4">
            {/* Loading */}
            {isLoading && (
              <div className="space-y-2" data-testid="filters-loading">
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-3/4" />
              </div>
            )}

            {/* Error */}
            {!isLoading && error && (
              <p className="text-sm text-destructive" data-testid="filters-error">
                Failed to load source filters: {error.message}
              </p>
            )}

            {/* Table */}
            {!isLoading && !error && (
              <FiltersTable
                filters={filters}
                onEdit={handleEdit}
                onDelete={handleDelete}
                editingId={editingFilter?.id ?? null}
              />
            )}

            {/* Inline edit form */}
            {editingFilter && (
              <EditFilterForm
                key={editingFilter.id}
                filter={editingFilter}
                onSaved={handleEditSaved}
                onCancel={handleEditCancel}
              />
            )}

            {/* Create form or button */}
            {!showCreateForm && !editingFilter && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowCreateForm(true)}
                data-testid="show-create-filter-btn"
              >
                <Plus className="size-3.5 mr-1" />
                Create filter
              </Button>
            )}

            {showCreateForm && (
              <CreateFilterForm
                onCreated={handleCreateCreated}
                onCancel={handleCreateCancel}
              />
            )}
          </div>
        </SheetContent>
      </Sheet>

      <DeleteConfirmDialog
        open={deleteOpen}
        onOpenChange={(open) => {
          setDeleteOpen(open);
          if (!open) setDeleteFilter(null);
        }}
        filter={deleteFilter}
        onConfirm={handleDeleteConfirm}
      />
    </>
  );
}
