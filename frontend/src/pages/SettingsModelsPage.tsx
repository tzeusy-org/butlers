/**
 * SettingsModelsPage — /settings/models
 *
 * Renders the global model catalog in the Dispatch design language:
 *   - Tier-grouped sections in canonical order (reasoning, workhorse, cheap, specialty, local, legacy)
 *   - Per-row priority stepper (↑/↓) backed by PUT /api/settings/models/{id}/priority
 *   - Enable toggle backed by PUT /api/settings/models/{id}
 *   - Test / Edit / Delete row actions
 *   - State and tier filter chips
 *   - "New model" button backed by POST /api/settings/models
 *   - "Verify All" button backed by POST /api/settings/models/verify-all
 *   - Dev-mode ApiWireFooter showing endpoints this page hits (§4.5)
 *
 * Design refs:
 *   (settings dispatch redesign, graduated) settings-redesign.jsx :: ModelCatalogExpanded
 *
 * bu-q2nz3 — Phase 2: /settings/models page
 */

import { useState } from "react";
import { toast } from "sonner";
import { RotateCcw } from "lucide-react";

import { ApiError } from "@/api/index.ts";
import type { ComplexityTier, ModelCatalogEntry } from "@/api/types.ts";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useCreateModelCatalogEntry,
  useDeleteModelCatalogEntry,
  useModelCatalog,
  useModelUsageDetail,
  useResetModelUsage,
  useSetModelTokenLimits,
  useTestModelCatalogEntry,
  useUpdateModelCatalogEntry,
  useUpdateModelPriority,
  useVerifyAllModels,
} from "@/hooks/use-model-catalog";
import type { UsageWindow } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Canonical tier order per spec §3.3 and design §D4. */
const TIER_ORDER: ComplexityTier[] = [
  "reasoning",
  "workhorse",
  "cheap",
  "specialty",
  "local",
  "legacy",
];

/** Human-readable tier labels. */
const TIER_LABEL: Record<ComplexityTier, string> = {
  reasoning: "Reasoning",
  workhorse: "Workhorse",
  cheap: "Cheap",
  specialty: "Specialty",
  local: "Local",
  legacy: "Legacy",
};

/**
 * Known runtime backends a catalog entry can dispatch to.
 *
 * This list is the frontend's single source of truth and mirrors the
 * `runtime_type` values present in `model_catalog_defaults.toml`. When a new
 * runtime backend is added to the toml, add it here too so the create/edit
 * dropdowns stay in sync. `runtime_type` is a free string server-side (no
 * enum), so the backend does not validate membership in this set.
 */
const RUNTIME_TYPES = ["claude", "codex", "gemini", "opencode"] as const;

/** Default per-session timeout (seconds) for a brand-new catalog entry. */
const DEFAULT_SESSION_TIMEOUT_S = 1800;

/**
 * Pre-configured alias templates for the "Add Model" dialog.
 * Each template pre-fills runtime_type and extra_args; form fields remain editable.
 */
const ALIAS_TEMPLATES = [
  {
    label: "Codex with reasoning effort",
    runtimeType: "codex",
    extraArgs: ["--config", "model_reasoning_effort=high"],
  },
  {
    label: "Claude with extended thinking",
    runtimeType: "claude",
    extraArgs: ["--thinking-budget", "8000"],
  },
] as const;

type StateFilter = "all" | "verified" | "attention" | "offline" | "deprecated";

// ---------------------------------------------------------------------------
// Token-usage formatting helpers (spec: catalog-token-limits §"Dashboard Usage
// Columns")
// ---------------------------------------------------------------------------

/** Compact token count for the bar label: 142312 → "142K", 4_500_000 → "4.5M". */
function formatCompactTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${Math.round(n / 1000)}K`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

/** Percent of a window's limit consumed, or null when no limit is configured. */
function usagePercent(usage: number, limit: number | null): number | null {
  if (limit === null || limit <= 0) return null;
  return (usage / limit) * 100;
}

/**
 * Progress-bar fill color per spec thresholds:
 * green 0–60%, yellow 60–85%, red 85–100%, red when over (BLOCKED).
 */
function barColorClass(percent: number): string {
  if (percent >= 85) return "bg-red-500";
  if (percent >= 60) return "bg-yellow-500";
  return "bg-green-500";
}

/** Coarse "Nm ago" / "Nh ago" / "Nd ago" relative time for the reset tooltip. */
function relativeTimeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const sec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

// ---------------------------------------------------------------------------
// ExtraArgsEditor — key-value editor for CLI args with a raw-JSON toggle
//
// Spec (§ "Extra args key-value editor"):
//   - Each row has a single text input for one CLI token (e.g. "--config" or "key=value")
//   - "Add arg" button appends a new empty row
//   - Each row has a remove (×) button
//   - A "Raw JSON" toggle switches to a textarea for direct JSON-array editing
//
// Design contract:
//   - The component is fully controlled via `value: string[]` / `onChange`.
//   - In raw-JSON mode, edits are applied live when the JSON parses correctly.
//   - While raw-JSON has a parse error, `onErrorChange(true)` is called so the
//     parent can block form submission. ExtraArgsEditor renders its own error
//     message so the parent must NOT duplicate it.
// ---------------------------------------------------------------------------

interface ExtraArgsEditorProps {
  value: string[];
  onChange: (args: string[]) => void;
  /** Called whenever the raw-mode parse state changes (true = has error). */
  onErrorChange?: (hasError: boolean) => void;
  /** Prefix used for unique element IDs. */
  idPrefix?: string;
}

function ExtraArgsEditor({
  value,
  onChange,
  onErrorChange,
  idPrefix = "args",
}: ExtraArgsEditorProps) {
  const [rawMode, setRawMode] = useState(false);
  const [rawText, setRawText] = useState("");
  const [rawError, setRawError] = useState("");

  // Detect externally-driven value changes (e.g., a template applied from the parent)
  // while the user is in raw-JSON mode and sync rawText accordingly.
  // Pattern: track `prevValue` in state, compare on every render, and call setState
  // inside the conditional to schedule an immediate re-render with the corrected text.
  // See https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes
  const [prevValue, setPrevValue] = useState(value);
  if (value !== prevValue) {
    setPrevValue(value);
    if (rawMode) {
      // Guard: skip reset if rawText already encodes the new value — that means the
      // change came from our own typing (handleRawChange → onChange → parent setState).
      let alreadySynced = false;
      try {
        const parsed = JSON.parse(rawText);
        if (Array.isArray(parsed) && JSON.stringify(parsed) === JSON.stringify(value)) {
          alreadySynced = true;
        }
      } catch {
        // rawText is invalid JSON — fall through to reset
      }
      if (!alreadySynced) {
        setRawText(value.length > 0 ? JSON.stringify(value) : "");
        setRawError("");
      }
    }
  }

  // Enter raw mode: serialize current string[] to JSON text.
  const enterRawMode = () => {
    setRawText(value.length > 0 ? JSON.stringify(value) : "");
    setRawError("");
    setRawMode(true);
  };

  // Leave raw mode: only if no current parse error.
  const exitRawMode = () => {
    if (rawError) return; // block exit while invalid
    setRawMode(false);
  };

  // Live-validate raw text and propagate parsed value to parent.
  const handleRawChange = (text: string) => {
    setRawText(text);
    if (!text.trim()) {
      setRawError("");
      onErrorChange?.(false);
      onChange([]);
      return;
    }
    try {
      const parsed = JSON.parse(text);
      if (!Array.isArray(parsed)) {
        setRawError("Must be a JSON array");
        onErrorChange?.(true);
      } else if (!parsed.every((item) => typeof item === "string")) {
        setRawError("All elements must be strings");
        onErrorChange?.(true);
      } else {
        setRawError("");
        onErrorChange?.(false);
        onChange(parsed as string[]);
      }
    } catch {
      setRawError("Invalid JSON");
      onErrorChange?.(true);
    }
  };

  const addRow = () => onChange([...value, ""]);
  const removeRow = (i: number) => onChange(value.filter((_, idx) => idx !== i));
  const updateRow = (i: number, v: string) => {
    const next = [...value];
    next[i] = v;
    onChange(next);
  };

  return (
    <div className="grid gap-1.5">
      <div className="flex items-center justify-between">
        <Label className="font-mono text-[11px] uppercase tracking-widest">Args</Label>
        <button
          type="button"
          onClick={rawMode ? exitRawMode : enterRawMode}
          aria-label={rawMode ? "Switch to key-value editor" : "Switch to raw JSON editor"}
          className="font-mono text-[9px] uppercase tracking-widest text-muted-foreground
            hover:text-foreground transition-colors"
        >
          {rawMode ? "← KV editor" : "Raw JSON →"}
        </button>
      </div>

      {rawMode ? (
        <>
          <Textarea
            id={`${idPrefix}-raw`}
            aria-label="Args (JSON array)"
            value={rawText}
            onChange={(e) => handleRawChange(e.target.value)}
            placeholder='e.g. ["--max-turns", "10"]'
            rows={3}
            aria-invalid={!!rawError}
            className="font-mono text-xs resize-y"
          />
          {rawError && (
            <p className="font-mono text-[10px] text-destructive">{rawError}</p>
          )}
        </>
      ) : (
        <div className="grid gap-2">
          {value.map((token, i) => (
            <div key={i} className="flex items-center gap-2">
              <Input
                value={token}
                onChange={(e) => updateRow(i, e.target.value)}
                placeholder="CLI arg or key=value"
                aria-label={`Arg ${i + 1}`}
                className="font-mono text-xs flex-1"
              />
              <button
                type="button"
                onClick={() => removeRow(i)}
                aria-label={`Remove arg ${i + 1}`}
                className="w-5 h-5 flex items-center justify-center text-muted-foreground
                  hover:text-destructive transition-colors shrink-0"
              >
                ×
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={addRow}
            className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground
              hover:text-foreground transition-colors text-left"
          >
            + Add arg
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// DeleteConfirmDialog — replaces window.confirm() for catalog entry deletion.
//
// Spec (§ "Delete with dependency check"):
//   A confirmation dialog warns that butler overrides referencing the entry will
//   be cascade-deleted, so the operator makes an informed choice.
// ---------------------------------------------------------------------------

interface DeleteConfirmDialogProps {
  model: ModelCatalogEntry;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  isPending: boolean;
}

function DeleteConfirmDialog({
  model,
  open,
  onOpenChange,
  onConfirm,
  isPending,
}: DeleteConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle className="font-mono text-sm">
            Delete model: <span className="text-muted-foreground">{model.alias}</span>
          </DialogTitle>
          <DialogDescription className="font-mono text-[11px] leading-relaxed">
            <span className="text-destructive font-medium">
              This action is permanent and cannot be undone.
            </span>{" "}
            Removing <span className="font-medium">{model.alias}</span> from the catalog will
            cascade-delete any butler override entries that reference it. Butlers that relied on
            this entry will revert to their next eligible tier model.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={isPending}
            className="font-mono text-[10px] uppercase tracking-widest"
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={onConfirm}
            disabled={isPending}
            className="font-mono text-[10px] uppercase tracking-widest"
          >
            {isPending ? "Deleting…" : "Delete →"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Edit model dialog
// ---------------------------------------------------------------------------

interface EditModelDialogProps {
  /** The catalog entry to edit. Each ModelRow owns exactly one dialog instance. */
  model: ModelCatalogEntry;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Inner form component. Mounted only when the dialog is open so that `useState`
 * always initializes from current `model` props — no `useEffect` sync needed.
 */
function EditModelForm({
  model,
  onOpenChange,
}: {
  model: ModelCatalogEntry;
  onOpenChange: (open: boolean) => void;
}) {
  const updateEntry = useUpdateModelCatalogEntry();

  const [alias, setAlias] = useState(model.alias);
  const [runtimeType, setRuntimeType] = useState(model.runtime_type);
  const [modelId, setModelId] = useState(model.model_id);
  const [complexityTier, setComplexityTier] = useState<ComplexityTier>(model.complexity_tier);
  const [priority, setPriority] = useState(String(model.priority));
  const [sessionTimeoutS, setSessionTimeoutS] = useState(String(model.session_timeout_s));
  const [enabled, setEnabled] = useState(model.enabled);
  // args is stored as string[] — ExtraArgsEditor serializes/deserializes raw JSON internally.
  const [args, setArgs] = useState<string[]>(model.extra_args);
  // True when ExtraArgsEditor's raw-JSON mode has a parse error.
  const [argsHasError, setArgsHasError] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const validate = (): boolean => {
    const errors: Record<string, string> = {};
    if (!alias.trim()) errors.alias = "Alias is required";
    if (!runtimeType.trim()) errors.runtime_type = "Runtime type is required";
    if (!modelId.trim()) errors.model_id = "Model ID is required";
    if (!TIER_ORDER.includes(complexityTier))
      errors.complexity_tier = "Must be one of the six canonical tiers";
    const parsedPriority = parseInt(priority, 10);
    if (isNaN(parsedPriority) || parsedPriority < 0)
      errors.priority = "Priority must be a non-negative integer";
    const parsedTimeout = parseInt(sessionTimeoutS, 10);
    if (isNaN(parsedTimeout) || parsedTimeout <= 0)
      errors.session_timeout_s = "Session timeout must be a positive integer (seconds)";
    // args is already string[]; raw-mode parse errors are shown by ExtraArgsEditor
    // and tracked via argsHasError. Block submission but don't show a duplicate message.
    if (argsHasError) errors._argsRaw = "invalid";
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSave = () => {
    if (!validate()) return;

    // args is already a valid string[]; no JSON.parse needed.
    const extraArgs = args;

    updateEntry.mutate(
      {
        id: model.id,
        body: {
          alias: alias.trim(),
          runtime_type: runtimeType.trim(),
          model_id: modelId.trim(),
          complexity_tier: complexityTier,
          priority: parseInt(priority, 10),
          session_timeout_s: parseInt(sessionTimeoutS, 10),
          enabled,
          extra_args: extraArgs,
        },
      },
      {
        onSuccess: () => {
          toast.success(`Saved changes to ${alias.trim()}`);
          onOpenChange(false);
        },
        onError: (err) => {
          const msg =
            err instanceof ApiError && err.status === 422
              ? "Validation error: check your inputs"
              : err instanceof Error
                ? err.message
                : "Failed to save model";
          toast.error(msg);
        },
      },
    );
  };

  return (
    <>
      <div className="grid gap-4 py-2">
        {/* Alias — warning shown when the alias is changed from its original value */}
        <div className="grid gap-1.5">
          <Label htmlFor="edit-alias" className="font-mono text-[11px] uppercase tracking-widest">
            Alias
          </Label>
          <Input
            id="edit-alias"
            value={alias}
            onChange={(e) => setAlias(e.target.value)}
            placeholder="e.g. claude-sonnet"
            aria-invalid={!!fieldErrors.alias}
            className="font-mono text-sm"
          />
          {fieldErrors.alias && (
            <p className="font-mono text-[10px] text-destructive">{fieldErrors.alias}</p>
          )}
          {alias !== model.alias && !fieldErrors.alias && (
            <p className="font-mono text-[10px] text-amber-600 dark:text-amber-400">
              Warning: Changing the alias may break existing butler override references.
            </p>
          )}
        </div>

        {/* Runtime type */}
        <div className="grid gap-1.5">
          <Label
            htmlFor="edit-runtime"
            className="font-mono text-[11px] uppercase tracking-widest"
          >
            Runtime type
          </Label>
          <Select value={runtimeType} onValueChange={setRuntimeType}>
            <SelectTrigger id="edit-runtime" className="font-mono text-sm w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(
                (RUNTIME_TYPES as readonly string[]).includes(model.runtime_type)
                  ? RUNTIME_TYPES
                  : [...RUNTIME_TYPES, model.runtime_type]
              ).map((rt) => (
                <SelectItem key={rt} value={rt} className="font-mono text-sm">
                  {rt}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {fieldErrors.runtime_type && (
            <p className="font-mono text-[10px] text-destructive">{fieldErrors.runtime_type}</p>
          )}
        </div>

        {/* Model ID */}
        <div className="grid gap-1.5">
          <Label
            htmlFor="edit-model-id"
            className="font-mono text-[11px] uppercase tracking-widest"
          >
            Model ID
          </Label>
          <Input
            id="edit-model-id"
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            placeholder="e.g. claude-sonnet-4-6"
            aria-invalid={!!fieldErrors.model_id}
            className="font-mono text-sm"
          />
          {fieldErrors.model_id && (
            <p className="font-mono text-[10px] text-destructive">{fieldErrors.model_id}</p>
          )}
        </div>

        {/* Complexity tier */}
        <div className="grid gap-1.5">
          <Label
            htmlFor="edit-tier"
            className="font-mono text-[11px] uppercase tracking-widest"
          >
            Complexity tier
          </Label>
          <Select
            value={complexityTier}
            onValueChange={(v) => setComplexityTier(v as ComplexityTier)}
          >
            <SelectTrigger id="edit-tier" className="font-mono text-sm w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TIER_ORDER.map((t) => (
                <SelectItem key={t} value={t} className="font-mono text-sm">
                  {TIER_LABEL[t]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {fieldErrors.complexity_tier && (
            <p className="font-mono text-[10px] text-destructive">{fieldErrors.complexity_tier}</p>
          )}
        </div>

        {/* Priority */}
        <div className="grid gap-1.5">
          <Label
            htmlFor="edit-priority"
            className="font-mono text-[11px] uppercase tracking-widest"
          >
            Priority
          </Label>
          <Input
            id="edit-priority"
            type="number"
            min={0}
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            aria-invalid={!!fieldErrors.priority}
            className="font-mono text-sm"
          />
          {fieldErrors.priority && (
            <p className="font-mono text-[10px] text-destructive">{fieldErrors.priority}</p>
          )}
        </div>

        {/* Session timeout (seconds) */}
        <div className="grid gap-1.5">
          <Label
            htmlFor="edit-session-timeout"
            className="font-mono text-[11px] uppercase tracking-widest"
          >
            Per-session timeout (s)
          </Label>
          <Input
            id="edit-session-timeout"
            type="number"
            min={1}
            step={1}
            value={sessionTimeoutS}
            onChange={(e) => setSessionTimeoutS(e.target.value)}
            aria-invalid={!!fieldErrors.session_timeout_s}
            className="font-mono text-sm"
          />
          {fieldErrors.session_timeout_s && (
            <p className="font-mono text-[10px] text-destructive">
              {fieldErrors.session_timeout_s}
            </p>
          )}
        </div>

        {/* Enabled toggle */}
        <div className="flex items-center gap-3">
          <Switch
            id="edit-enabled"
            checked={enabled}
            onCheckedChange={setEnabled}
            aria-label="Enabled"
          />
          <Label
            htmlFor="edit-enabled"
            className="font-mono text-[11px] uppercase tracking-widest cursor-pointer"
          >
            {enabled ? "Enabled" : "Disabled"}
          </Label>
        </div>

        {/* Args — key-value editor; "Raw JSON →" toggle switches to textarea */}
        <ExtraArgsEditor
          value={args}
          onChange={setArgs}
          onErrorChange={setArgsHasError}
          idPrefix="edit-args"
        />
      </div>

      <DialogFooter>
        <Button
          variant="outline"
          size="sm"
          onClick={() => onOpenChange(false)}
          disabled={updateEntry.isPending}
          className="font-mono text-[10px] uppercase tracking-widest"
        >
          Cancel
        </Button>
        <Button
          size="sm"
          onClick={handleSave}
          disabled={updateEntry.isPending}
          className="font-mono text-[10px] uppercase tracking-widest"
        >
          {updateEntry.isPending ? "Saving…" : "Save →"}
        </Button>
      </DialogFooter>
    </>
  );
}

function EditModelDialog({ model, open, onOpenChange }: EditModelDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle className="font-mono text-sm">
            Edit model: <span className="text-muted-foreground">{model.alias}</span>
          </DialogTitle>
          <DialogDescription className="font-mono text-[11px]">
            Edit the configuration for this model catalog entry.
          </DialogDescription>
        </DialogHeader>
        {open && <EditModelForm model={model} onOpenChange={onOpenChange} />}
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Add model dialog
// ---------------------------------------------------------------------------

/**
 * Inner form for creating a brand-new catalog entry. Mounted only when the
 * dialog is open so `useState` initializes once from the defaults below.
 *
 * Exposes `runtime_type` (required by POST /api/settings/models) and seeds
 * sensible defaults for the optional fields.
 */
function AddModelForm({ onOpenChange }: { onOpenChange: (open: boolean) => void }) {
  const createEntry = useCreateModelCatalogEntry();

  const [alias, setAlias] = useState("");
  const [runtimeType, setRuntimeType] = useState<string>(RUNTIME_TYPES[0]);
  const [modelId, setModelId] = useState("");
  const [complexityTier, setComplexityTier] = useState<ComplexityTier>("workhorse");
  const [priority, setPriority] = useState("0");
  const [sessionTimeoutS, setSessionTimeoutS] = useState(String(DEFAULT_SESSION_TIMEOUT_S));
  const [enabled, setEnabled] = useState(true);
  // args is stored as string[] — ExtraArgsEditor serializes/deserializes raw JSON internally.
  const [args, setArgs] = useState<string[]>([]);
  // True when ExtraArgsEditor's raw-JSON mode has a parse error.
  const [argsHasError, setArgsHasError] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  /** Apply a pre-configured alias template to the form fields. */
  const applyTemplate = (label: string) => {
    const tpl = ALIAS_TEMPLATES.find((t) => t.label === label);
    if (!tpl) return;
    setRuntimeType(tpl.runtimeType);
    setArgs([...tpl.extraArgs]);
  };

  const validate = (): boolean => {
    const errors: Record<string, string> = {};
    if (!alias.trim()) errors.alias = "Alias is required";
    if (!runtimeType.trim()) errors.runtime_type = "Runtime type is required";
    if (!modelId.trim()) errors.model_id = "Model ID is required";
    if (!TIER_ORDER.includes(complexityTier))
      errors.complexity_tier = "Must be one of the six canonical tiers";
    const parsedPriority = parseInt(priority, 10);
    if (isNaN(parsedPriority) || parsedPriority < 0)
      errors.priority = "Priority must be a non-negative integer";
    const parsedTimeout = parseInt(sessionTimeoutS, 10);
    if (isNaN(parsedTimeout) || parsedTimeout <= 0)
      errors.session_timeout_s = "Session timeout must be a positive integer (seconds)";
    // args is already string[]; raw-mode parse errors are shown by ExtraArgsEditor.
    if (argsHasError) errors._argsRaw = "invalid";
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleCreate = () => {
    if (!validate()) return;

    // args is already a valid string[]; no JSON.parse needed.
    const extraArgs = args;

    createEntry.mutate(
      {
        alias: alias.trim(),
        runtime_type: runtimeType.trim(),
        model_id: modelId.trim(),
        complexity_tier: complexityTier,
        priority: parseInt(priority, 10),
        session_timeout_s: parseInt(sessionTimeoutS, 10),
        enabled,
        extra_args: extraArgs,
      },
      {
        onSuccess: () => {
          toast.success(`Added ${alias.trim()}`);
          onOpenChange(false);
        },
        onError: (err) => {
          let msg: string;
          if (err instanceof ApiError && err.status === 409) {
            msg = `A model with alias "${alias.trim()}" already exists`;
          } else if (err instanceof ApiError && err.status === 422) {
            msg = "Validation error: check your inputs";
          } else if (err instanceof Error) {
            msg = err.message;
          } else {
            msg = "Failed to add model";
          }
          toast.error(msg);
        },
      },
    );
  };

  return (
    <>
      <div className="grid gap-4 py-2">
        {/* Template selector — pre-fills runtimeType and extraArgs; fields remain editable */}
        <div className="grid gap-1.5">
          <Label htmlFor="add-template" className="font-mono text-[11px] uppercase tracking-widest">
            Use template
          </Label>
          <Select onValueChange={applyTemplate}>
            <SelectTrigger id="add-template" className="font-mono text-sm w-full">
              <SelectValue placeholder="— pick a template (optional)" />
            </SelectTrigger>
            <SelectContent>
              {ALIAS_TEMPLATES.map((tpl) => (
                <SelectItem key={tpl.label} value={tpl.label} className="font-mono text-sm">
                  {tpl.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Alias */}
        <div className="grid gap-1.5">
          <Label htmlFor="add-alias" className="font-mono text-[11px] uppercase tracking-widest">
            Alias
          </Label>
          <Input
            id="add-alias"
            value={alias}
            onChange={(e) => setAlias(e.target.value)}
            placeholder="e.g. claude-sonnet"
            aria-invalid={!!fieldErrors.alias}
            className="font-mono text-sm"
          />
          {fieldErrors.alias && (
            <p className="font-mono text-[10px] text-destructive">{fieldErrors.alias}</p>
          )}
        </div>

        {/* Runtime type */}
        <div className="grid gap-1.5">
          <Label
            htmlFor="add-runtime"
            className="font-mono text-[11px] uppercase tracking-widest"
          >
            Runtime type
          </Label>
          <Select value={runtimeType} onValueChange={setRuntimeType}>
            <SelectTrigger id="add-runtime" className="font-mono text-sm w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {RUNTIME_TYPES.map((rt) => (
                <SelectItem key={rt} value={rt} className="font-mono text-sm">
                  {rt}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {fieldErrors.runtime_type && (
            <p className="font-mono text-[10px] text-destructive">{fieldErrors.runtime_type}</p>
          )}
        </div>

        {/* Model ID */}
        <div className="grid gap-1.5">
          <Label
            htmlFor="add-model-id"
            className="font-mono text-[11px] uppercase tracking-widest"
          >
            Model ID
          </Label>
          <Input
            id="add-model-id"
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            placeholder="e.g. claude-sonnet-4-6"
            aria-invalid={!!fieldErrors.model_id}
            className="font-mono text-sm"
          />
          {fieldErrors.model_id && (
            <p className="font-mono text-[10px] text-destructive">{fieldErrors.model_id}</p>
          )}
        </div>

        {/* Complexity tier */}
        <div className="grid gap-1.5">
          <Label htmlFor="add-tier" className="font-mono text-[11px] uppercase tracking-widest">
            Complexity tier
          </Label>
          <Select
            value={complexityTier}
            onValueChange={(v) => setComplexityTier(v as ComplexityTier)}
          >
            <SelectTrigger id="add-tier" className="font-mono text-sm w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TIER_ORDER.map((t) => (
                <SelectItem key={t} value={t} className="font-mono text-sm">
                  {TIER_LABEL[t]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {fieldErrors.complexity_tier && (
            <p className="font-mono text-[10px] text-destructive">{fieldErrors.complexity_tier}</p>
          )}
        </div>

        {/* Priority */}
        <div className="grid gap-1.5">
          <Label htmlFor="add-priority" className="font-mono text-[11px] uppercase tracking-widest">
            Priority
          </Label>
          <Input
            id="add-priority"
            type="number"
            min={0}
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            aria-invalid={!!fieldErrors.priority}
            className="font-mono text-sm"
          />
          {fieldErrors.priority && (
            <p className="font-mono text-[10px] text-destructive">{fieldErrors.priority}</p>
          )}
        </div>

        {/* Session timeout (seconds) */}
        <div className="grid gap-1.5">
          <Label
            htmlFor="add-session-timeout"
            className="font-mono text-[11px] uppercase tracking-widest"
          >
            Per-session timeout (s)
          </Label>
          <Input
            id="add-session-timeout"
            type="number"
            min={1}
            step={1}
            value={sessionTimeoutS}
            onChange={(e) => setSessionTimeoutS(e.target.value)}
            aria-invalid={!!fieldErrors.session_timeout_s}
            className="font-mono text-sm"
          />
          {fieldErrors.session_timeout_s && (
            <p className="font-mono text-[10px] text-destructive">
              {fieldErrors.session_timeout_s}
            </p>
          )}
        </div>

        {/* Enabled toggle */}
        <div className="flex items-center gap-3">
          <Switch
            id="add-enabled"
            checked={enabled}
            onCheckedChange={setEnabled}
            aria-label="Enabled"
          />
          <Label
            htmlFor="add-enabled"
            className="font-mono text-[11px] uppercase tracking-widest cursor-pointer"
          >
            {enabled ? "Enabled" : "Disabled"}
          </Label>
        </div>

        {/* Args — key-value editor; "Raw JSON →" toggle switches to textarea */}
        <ExtraArgsEditor
          value={args}
          onChange={setArgs}
          onErrorChange={setArgsHasError}
          idPrefix="add-args"
        />
      </div>

      <DialogFooter>
        <Button
          variant="outline"
          size="sm"
          onClick={() => onOpenChange(false)}
          disabled={createEntry.isPending}
          className="font-mono text-[10px] uppercase tracking-widest"
        >
          Cancel
        </Button>
        <Button
          size="sm"
          onClick={handleCreate}
          disabled={createEntry.isPending}
          className="font-mono text-[10px] uppercase tracking-widest"
        >
          {createEntry.isPending ? "Adding…" : "Add model →"}
        </Button>
      </DialogFooter>
    </>
  );
}

function AddModelDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle className="font-mono text-sm">Add model</DialogTitle>
          <DialogDescription className="font-mono text-[11px]">
            Register a new entry in the shared model catalog.
          </DialogDescription>
        </DialogHeader>
        {open && <AddModelForm onOpenChange={onOpenChange} />}
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Filter chip sub-component
// ---------------------------------------------------------------------------

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={[
        "px-2 py-0.5 rounded text-[10px] font-mono uppercase tracking-widest border",
        "transition-colors cursor-pointer",
        active
          ? "bg-foreground text-background border-foreground"
          : "bg-transparent text-muted-foreground border-border hover:border-foreground/40",
      ].join(" ")}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Tier section header
// ---------------------------------------------------------------------------

function TierHeader({
  tier,
  count,
}: {
  tier: ComplexityTier;
  count: number;
}) {
  return (
    <div className="flex items-baseline gap-3 px-4 py-2 border-b border-border bg-muted/30">
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-foreground">
        {TIER_LABEL[tier]}
      </span>
      <span className="font-mono text-[10px] text-muted-foreground">
        {count} {count === 1 ? "model" : "models"}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Priority stepper
// ---------------------------------------------------------------------------

function PriorityStepper({
  entryId,
  priority,
}: {
  entryId: string;
  priority: number;
}) {
  const updatePriority = useUpdateModelPriority();

  const step = (delta: number) => {
    updatePriority.mutate(
      { id: entryId, body: { delta } },
      {
        onError: () => toast.error("Failed to update priority"),
      },
    );
  };

  return (
    <div className="flex items-center gap-1">
      <button
        onClick={() => step(-1)}
        disabled={priority === 0 || updatePriority.isPending}
        className="w-5 h-5 flex items-center justify-center rounded font-mono text-xs
          border border-border text-muted-foreground hover:text-foreground
          hover:border-foreground/40 disabled:opacity-30 disabled:cursor-not-allowed
          transition-colors"
        title="Decrease priority"
      >
        ↓
      </button>
      <span className="font-mono text-[11px] w-6 text-center tabular-nums">
        {priority}
      </span>
      <button
        onClick={() => step(1)}
        disabled={updatePriority.isPending}
        className="w-5 h-5 flex items-center justify-center rounded font-mono text-xs
          border border-border text-muted-foreground hover:text-foreground
          hover:border-foreground/40 disabled:opacity-30 disabled:cursor-not-allowed
          transition-colors"
        title="Increase priority"
      >
        ↑
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Usage cell — one rolling window (24h or 30d) per spec "Dashboard Usage
// Columns": progress bar + thresholds + BLOCKED badge + reset + tooltip +
// inline limit editing.
// ---------------------------------------------------------------------------

function UsageCell({
  model,
  window,
  usage,
  limit,
}: {
  model: ModelCatalogEntry;
  window: UsageWindow & ("24h" | "30d");
  usage: number;
  limit: number | null;
}) {
  const setLimits = useSetModelTokenLimits();
  const resetUsage = useResetModelUsage();

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [tipOpen, setTipOpen] = useState(false);

  // Detailed usage (reset timestamps) is fetched lazily, only while hovered.
  const { data: detail } = useModelUsageDetail(model.id, tipOpen);
  const resetAt =
    window === "24h" ? detail?.data.reset_24h_at ?? null : detail?.data.reset_30d_at ?? null;

  const percent = usagePercent(usage, limit);
  const blocked = percent !== null && percent > 100;
  const windowLabel = window === "24h" ? "Rolling 24h window" : "Rolling 30d window";

  // Accessible-name / tooltip contract (exact counts, percent, window label).
  const tooltipText = [
    `${usage.toLocaleString()} / ${limit !== null ? limit.toLocaleString() : "∞"} tokens`,
    percent !== null ? `${Math.round(percent)}% used` : "no limit set",
    windowLabel,
  ].join(" · ");

  const startEdit = () => {
    setDraft(limit !== null ? String(limit) : "");
    setEditing(true);
  };

  const handleSaveLimit = () => {
    // Guard against double submission (e.g. Enter then onBlur) while a save is in flight.
    if (setLimits.isPending) return;

    const trimmed = draft.trim();
    let parsed: number | null;
    if (trimmed === "" || trimmed === "-") {
      parsed = null;
    } else {
      const n = Math.round(Number(trimmed));
      if (!Number.isFinite(n) || n < 0) {
        toast.error("Limit must be a non-negative integer (or blank for unlimited)");
        return;
      }
      parsed = n;
    }

    // No-op when the value is unchanged — avoids redundant network/database writes.
    if (parsed === limit) {
      setEditing(false);
      return;
    }

    const body =
      window === "24h"
        ? { limit_24h: parsed, limit_30d: model.limit_30d }
        : { limit_24h: model.limit_24h, limit_30d: parsed };

    setLimits.mutate(
      { id: model.id, body },
      {
        onSuccess: () => {
          toast.success(
            `Set ${window} limit for ${model.alias} to ${parsed !== null ? parsed.toLocaleString() : "unlimited"}`,
          );
          setEditing(false);
        },
        onError: () => toast.error(`Failed to set ${window} limit`),
      },
    );
  };

  const handleReset = () => {
    resetUsage.mutate(
      { id: model.id, body: { window } },
      {
        onSuccess: () => toast.success(`Reset ${window} usage for ${model.alias}`),
        onError: () => toast.error(`Failed to reset ${window} usage`),
      },
    );
  };

  return (
    <div className="flex flex-col gap-1 min-w-[128px]">
      {/* Header line: window label · BLOCKED badge · reset */}
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-[8px] uppercase tracking-widest text-muted-foreground">
          {window}
        </span>
        {blocked && (
          <span className="font-mono text-[8px] uppercase tracking-widest px-1 rounded bg-red-500 text-white">
            BLOCKED
          </span>
        )}
        <button
          type="button"
          onClick={handleReset}
          disabled={resetUsage.isPending}
          aria-label={`Reset ${window} usage for ${model.alias}`}
          title={`Reset ${window} usage`}
          className="ml-auto text-muted-foreground hover:text-foreground disabled:opacity-40
            transition-colors"
        >
          <RotateCcw className="w-3 h-3" />
        </button>
      </div>

      {/* Progress bar (only when a limit is configured) — wrapped in a tooltip.
          The single TooltipProvider lives at the page root (SettingsModelsPage). */}
        <Tooltip open={tipOpen} onOpenChange={setTipOpen}>
          <TooltipTrigger asChild>
            <button
              type="button"
              aria-label={tooltipText}
              className="block w-full cursor-default"
            >
              {limit !== null ? (
                <div className="h-1.5 w-full rounded bg-muted overflow-hidden">
                  <div
                    className={`h-full rounded ${barColorClass(percent ?? 0)}`}
                    style={{ width: `${Math.min(100, percent ?? 0)}%` }}
                  />
                </div>
              ) : (
                <div className="h-1.5 w-full rounded border border-dashed border-border/60" />
              )}
            </button>
          </TooltipTrigger>
          <TooltipContent>
            <div className="font-mono text-[10px] leading-relaxed">
              <div>
                {usage.toLocaleString()} / {limit !== null ? limit.toLocaleString() : "∞"} tokens
              </div>
              <div>{percent !== null ? `${Math.round(percent)}% used` : "no limit set"}</div>
              <div>{windowLabel}</div>
              {resetAt && <div>Last reset: {relativeTimeAgo(resetAt)}</div>}
            </div>
          </TooltipContent>
        </Tooltip>

      {/* used / limit text — clicking the limit opens the inline editor */}
      <div className="flex items-center gap-1 font-mono text-[10px] tabular-nums">
        <span className={blocked ? "text-red-500" : "text-muted-foreground"}>
          {formatCompactTokens(usage)}
        </span>
        <span className="text-muted-foreground">/</span>
        {editing ? (
          <Input
            autoFocus
            disabled={setLimits.isPending}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={handleSaveLimit}
            onKeyDown={(e) => {
              // Blur on Enter so the single onBlur handler performs the save —
              // avoids a duplicate request from Enter + the subsequent blur.
              if (e.key === "Enter") e.currentTarget.blur();
              if (e.key === "Escape") setEditing(false);
            }}
            placeholder="—"
            aria-label={`Set ${window} limit for ${model.alias}`}
            className="h-5 w-16 px-1 font-mono text-[10px]"
          />
        ) : (
          <button
            type="button"
            onClick={startEdit}
            aria-label={`Set ${window} limit for ${model.alias}`}
            className="text-foreground/80 hover:text-foreground hover:underline underline-offset-2
              transition-colors"
          >
            {limit !== null ? formatCompactTokens(limit) : "-"}
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Model row
// ---------------------------------------------------------------------------

function ModelRow({ model }: { model: ModelCatalogEntry }) {
  const updateEntry = useUpdateModelCatalogEntry();
  const testEntry = useTestModelCatalogEntry();
  const deleteEntry = useDeleteModelCatalogEntry();
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const toggleEnabled = () => {
    updateEntry.mutate(
      { id: model.id, body: { enabled: !model.enabled } },
      { onError: () => toast.error("Failed to toggle model") },
    );
  };

  const handleTest = () => {
    testEntry.mutate(model.id, {
      onSuccess: (resp) => {
        if (resp.data.success) {
          toast.success(`${model.alias}: OK (${resp.data.duration_ms}ms)`);
        } else {
          toast.error(`${model.alias}: ${resp.data.error ?? "test failed"}`);
        }
      },
      onError: () => toast.error(`Failed to test ${model.alias}`),
    });
  };

  const handleDeleteConfirm = () => {
    deleteEntry.mutate(model.id, {
      onSuccess: () => {
        toast.success(`Deleted ${model.alias}`);
        setDeleteOpen(false);
      },
      onError: () => toast.error(`Failed to delete ${model.alias}`),
    });
  };

  const verificationStatus = model.last_verified_ok === true
    ? "verified"
    : model.last_verified_ok === false
      ? "error"
      : "untested";

  return (
    <div
      className={[
        "grid items-center gap-3 px-4 py-2.5 border-b border-border/50",
        "text-sm transition-colors hover:bg-muted/20",
        !model.enabled && "opacity-60",
      ]
        .filter(Boolean)
        .join(" ")}
      style={{ gridTemplateColumns: "auto 1fr auto auto auto auto auto auto" }}
    >
      {/* Priority stepper */}
      <PriorityStepper entryId={model.id} priority={model.priority} />

      {/* Model info */}
      <div className="min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-[12px] truncate">{model.alias}</span>
          {verificationStatus === "verified" && (
            <span className="font-mono text-[9px] uppercase tracking-widest text-green-600 dark:text-green-400 shrink-0">
              ✓
            </span>
          )}
          {verificationStatus === "error" && (
            <span className="font-mono text-[9px] uppercase tracking-widest text-destructive shrink-0">
              ✗
            </span>
          )}
        </div>
        <div className="font-mono text-[10px] text-muted-foreground truncate">
          {model.model_id} · {model.runtime_type}
        </div>
      </div>

      {/* Usage (rolling 24h) — bar + thresholds + reset + inline limit edit */}
      <UsageCell model={model} window="24h" usage={model.usage_24h} limit={model.limit_24h} />

      {/* Usage (rolling 30d) */}
      <UsageCell model={model} window="30d" usage={model.usage_30d} limit={model.limit_30d} />

      {/* Enable toggle */}
      <Switch
        checked={model.enabled}
        onCheckedChange={toggleEnabled}
        disabled={updateEntry.isPending}
        aria-label={`${model.enabled ? "Disable" : "Enable"} ${model.alias}`}
      />

      {/* Test action */}
      <button
        onClick={handleTest}
        disabled={testEntry.isPending}
        className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground
          hover:text-foreground transition-colors disabled:opacity-40 whitespace-nowrap"
      >
        Test →
      </button>

      {/* Edit action — opens full edit dialog */}
      <button
        onClick={() => setEditOpen(true)}
        className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground
          hover:text-foreground transition-colors whitespace-nowrap"
        aria-label={`Edit ${model.alias}`}
      >
        Edit →
      </button>

      {/* Delete action — opens confirmation dialog (not window.confirm) */}
      <button
        onClick={() => setDeleteOpen(true)}
        disabled={deleteEntry.isPending}
        aria-label={`Delete ${model.alias}`}
        className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground
          hover:text-destructive transition-colors disabled:opacity-40 whitespace-nowrap"
      >
        Delete →
      </button>

      {/* Edit dialog — rendered outside the grid row to avoid stacking context issues */}
      <EditModelDialog model={model} open={editOpen} onOpenChange={setEditOpen} />

      {/* Delete confirmation dialog */}
      <DeleteConfirmDialog
        model={model}
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        onConfirm={handleDeleteConfirm}
        isPending={deleteEntry.isPending}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty tier state (§4.4)
// ---------------------------------------------------------------------------

function EmptyTierState() {
  return (
    <div className="px-4 py-4">
      <p className="font-serif text-sm italic text-muted-foreground">
        Nothing in this tier.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dev-mode ApiWireFooter (§4.5)
// ---------------------------------------------------------------------------

function ApiWireFooter() {
  if (!import.meta.env.DEV) return null;

  const endpoints = [
    "GET /api/settings/models",
    "POST /api/settings/models",
    "PUT /api/settings/models/{id}/priority",
    "PUT /api/settings/models/{id}",
    "POST /api/settings/models/{id}/test",
    "DELETE /api/settings/models/{id}",
    "POST /api/settings/models/verify-all",
    "PUT /api/settings/models/{id}/limits",
    "POST /api/settings/models/{id}/reset-usage",
    "GET /api/settings/models/{id}/usage",
  ];

  return (
    <div className="mt-8 px-4 py-3 border border-border/50 rounded bg-muted/20">
      <p className="font-mono text-[9px] uppercase tracking-widest text-muted-foreground mb-2">
        dev · api wire
      </p>
      <ul className="space-y-0.5">
        {endpoints.map((ep) => (
          <li key={ep} className="font-mono text-[10px] text-muted-foreground">
            {ep}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function SettingsModelsPage() {
  const [tierFilter, setTierFilter] = useState<ComplexityTier | "all">("all");
  const [stateFilter, setStateFilter] = useState<StateFilter>("all");
  const [addOpen, setAddOpen] = useState(false);

  const { data, isLoading, isError } = useModelCatalog();
  const verifyAll = useVerifyAllModels();

  const entries: ModelCatalogEntry[] = data?.data ?? [];

  // Group by tier preserving canonical order
  const grouped = Object.fromEntries(
    TIER_ORDER.map((t) => [t, [] as ModelCatalogEntry[]]),
  ) as Record<ComplexityTier, ModelCatalogEntry[]>;

  for (const entry of entries) {
    const tier = entry.complexity_tier;
    if (grouped[tier]) {
      grouped[tier].push(entry);
    }
  }

  // Apply state filter
  const applyStateFilter = (rows: ModelCatalogEntry[]) => {
    if (stateFilter === "all") return rows;
    if (stateFilter === "verified") return rows.filter((m) => m.last_verified_ok === true);
    if (stateFilter === "attention") {
      return rows.filter((m) => m.last_verified_ok === false || m.last_verified_ok === null);
    }
    // offline / deprecated — not yet surfaced in this iteration
    return rows;
  };

  const visibleTiers: ComplexityTier[] = TIER_ORDER.filter(
    (t) => tierFilter === "all" || tierFilter === t,
  );

  // Counts for filter chips
  const totalCount = entries.length;
  const verifiedCount = entries.filter((m) => m.last_verified_ok === true).length;
  const tierCounts = Object.fromEntries(
    TIER_ORDER.map((t) => [t, grouped[t].length]),
  ) as Record<ComplexityTier, number>;

  const handleVerifyAll = () => {
    verifyAll.mutate(undefined, {
      onSuccess: (resp) => {
        const { ok, failed, total } = resp.data;
        toast.success(`Verified ${ok}/${total} models${failed > 0 ? ` · ${failed} failed` : ""}`);
      },
      onError: (err) => {
        if (err instanceof ApiError && err.status === 429) {
          toast.warning("Verify all was called recently. Wait 60 seconds before retrying.");
        } else {
          const msg = err instanceof Error ? err.message : "Verify all failed";
          toast.error(msg);
        }
      },
    });
  };

  return (
    <TooltipProvider>
      <div className="flex flex-col min-h-screen">
      {/* Breadcrumb */}
      <div className="px-7 py-3.5 border-b border-border flex items-baseline gap-3 font-mono text-[10px] text-muted-foreground uppercase tracking-[0.14em]">
        <span>butlers</span>
        <span>›</span>
        <span className="text-foreground/70">settings</span>
        <span>›</span>
        <span className="text-foreground">model catalog</span>
        <span className="ml-auto font-mono text-[10px] normal-case tracking-[0.04em] text-muted-foreground">
          {totalCount} models · {verifiedCount} verified · {TIER_ORDER.length} tiers
        </span>
      </div>

      {/* Page header */}
      <div className="px-7 py-5 border-b border-border grid grid-cols-[1fr_auto] gap-6 items-baseline">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground mb-2">
            settings · §1 · model catalog
          </p>
          <h1 className="text-3xl font-medium tracking-tight leading-tight">
            Every model the staff can call.
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleVerifyAll}
            disabled={verifyAll.isPending}
            className="font-mono text-[10px] uppercase tracking-widest"
          >
            {verifyAll.isPending ? "Verifying…" : "Verify all →"}
          </Button>
          <Button
            size="sm"
            onClick={() => setAddOpen(true)}
            className="font-mono text-[10px] uppercase tracking-widest"
          >
            New model →
          </Button>
        </div>
      </div>

      {/* Add model dialog */}
      <AddModelDialog open={addOpen} onOpenChange={setAddOpen} />

      {/* Filter bar */}
      <div className="px-7 py-2.5 border-b border-border flex items-center gap-4 flex-wrap font-mono text-[10px]">
        {/* Tier chips */}
        <div className="flex items-center gap-1.5">
          <span className="text-[9px] uppercase tracking-widest text-muted-foreground">tier</span>
          <FilterChip active={tierFilter === "all"} onClick={() => setTierFilter("all")}>
            all · {totalCount}
          </FilterChip>
          {TIER_ORDER.map((t) => (
            <FilterChip
              key={t}
              active={tierFilter === t}
              onClick={() => setTierFilter(t)}
            >
              {t} · {tierCounts[t]}
            </FilterChip>
          ))}
        </div>

        <div className="w-px h-4 bg-border" />

        {/* State chips */}
        <div className="flex items-center gap-1.5">
          <span className="text-[9px] uppercase tracking-widest text-muted-foreground">state</span>
          <FilterChip active={stateFilter === "all"} onClick={() => setStateFilter("all")}>
            all
          </FilterChip>
          <FilterChip
            active={stateFilter === "verified"}
            onClick={() => setStateFilter("verified")}
          >
            verified · {verifiedCount}
          </FilterChip>
          <FilterChip
            active={stateFilter === "attention"}
            onClick={() => setStateFilter("attention")}
          >
            attention
          </FilterChip>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-auto">
        {isLoading && (
          <div className="px-7 py-10">
            <p className="font-serif text-sm italic text-muted-foreground">Loading catalog…</p>
          </div>
        )}

        {isError && (
          <div className="px-7 py-10">
            <p className="font-serif text-sm italic text-destructive">
              Failed to load model catalog.
            </p>
          </div>
        )}

        {!isLoading && !isError && (
          <>
            {visibleTiers.map((tier) => {
              const rows = applyStateFilter(grouped[tier]);
              return (
                <section key={tier}>
                  <TierHeader tier={tier} count={grouped[tier].length} />
                  {rows.length === 0 ? (
                    <EmptyTierState />
                  ) : (
                    rows.map((model) => <ModelRow key={model.id} model={model} />)
                  )}
                </section>
              );
            })}

            {/* Footer */}
            <div className="px-7 py-5 flex items-baseline justify-between">
              <span className="font-mono text-[9.5px] text-muted-foreground normal-case tracking-[0.04em]">
                end of catalog · {totalCount} {totalCount === 1 ? "entry" : "entries"}
              </span>
              <button
                onClick={handleVerifyAll}
                disabled={verifyAll.isPending}
                className="font-mono text-[11px] text-muted-foreground hover:text-foreground
                  transition-colors disabled:opacity-40"
              >
                verify all now →
              </button>
            </div>
          </>
        )}

        <div className="px-7 pb-8">
          <ApiWireFooter />
        </div>
      </div>
      </div>
    </TooltipProvider>
  );
}
