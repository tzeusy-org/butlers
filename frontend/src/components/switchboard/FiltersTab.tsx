/**
 * FiltersTab — Filters tab content for the Ingestion page.
 *
 * Features:
 * - Rules table with priority, condition, action, match count, enabled toggle, CRUD actions
 * - Rule editor drawer for creating/editing rules with type-specific condition fields
 * - Test rule dry-run flow with result feedback
 * - Thread affinity panel: global enable/disable toggle and TTL input
 * - Gmail label filters panel: include/exclude editable tag inputs
 * - Import defaults (seed rules) flow with preview-before-confirm dialog
 */

import { useState } from "react";
import { AlertCircle, CheckCircle2, Edit2, FlaskConical, Loader2, Plus, Trash2 } from "lucide-react";

import type { TriageRule, TriageRuleCreate, TriageRuleType } from "@/api/types.ts";
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
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useCreateTriageRule,
  useDeleteTriageRule,
  useTestTriageRule,
  useThreadAffinitySettings,
  useTriageRules,
  useUpdateThreadAffinitySettings,
  useUpdateTriageRule,
} from "@/hooks/use-triage";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const RULE_TYPES: { value: TriageRuleType; label: string }[] = [
  { value: "sender_domain", label: "Sender Domain" },
  { value: "sender_address", label: "Sender Address" },
  { value: "header_condition", label: "Email Header" },
  { value: "mime_type", label: "MIME Attachment Type" },
];

const STATIC_ACTIONS = [
  { value: "skip", label: "Skip (tier 3)" },
  { value: "metadata_only", label: "Metadata only (tier 2)" },
  { value: "low_priority_queue", label: "Low priority queue" },
  { value: "pass_through", label: "Pass through to LLM" },
];

const AVAILABLE_BUTLERS = ["finance", "relationship", "health", "general", "travel", "calendar"];

const HEADER_OPS = [
  { value: "present", label: "is present" },
  { value: "equals", label: "equals" },
  { value: "contains", label: "contains" },
];

/** Seed rules per spec section 7 of pre_classification_triage.md. */
const SEED_RULES: TriageRuleCreate[] = [
  {
    rule_type: "sender_domain",
    condition: { domain: "chase.com", match: "suffix" },
    action: "route_to:finance",
    priority: 10,
  },
  {
    rule_type: "sender_domain",
    condition: { domain: "americanexpress.com", match: "suffix" },
    action: "route_to:finance",
    priority: 11,
  },
  {
    rule_type: "sender_domain",
    condition: { domain: "delta.com", match: "suffix" },
    action: "route_to:travel",
    priority: 20,
  },
  {
    rule_type: "sender_domain",
    condition: { domain: "united.com", match: "suffix" },
    action: "route_to:travel",
    priority: 21,
  },
  {
    rule_type: "sender_domain",
    condition: { domain: "paypal.com", match: "suffix" },
    action: "route_to:finance",
    priority: 30,
  },
  {
    rule_type: "header_condition",
    condition: { header: "List-Unsubscribe", op: "present", value: null },
    action: "metadata_only",
    priority: 40,
  },
  {
    rule_type: "header_condition",
    condition: { header: "Precedence", op: "equals", value: "bulk" },
    action: "low_priority_queue",
    priority: 41,
  },
  {
    rule_type: "header_condition",
    condition: { header: "Auto-Submitted", op: "equals", value: "auto-generated" },
    action: "skip",
    priority: 42,
  },
  {
    rule_type: "mime_type",
    condition: { type: "text/calendar" },
    action: "route_to:calendar",
    priority: 50,
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatAction(action: string): string {
  if (action.startsWith("route_to:")) {
    return `Route → ${action.slice("route_to:".length)}`;
  }
  switch (action) {
    case "skip":
      return "Skip";
    case "metadata_only":
      return "Metadata only";
    case "low_priority_queue":
      return "Low priority";
    case "pass_through":
      return "Pass through";
    default:
      return action;
  }
}

function formatCondition(ruleType: TriageRuleType, condition: Record<string, unknown>): string {
  switch (ruleType) {
    case "sender_domain":
      return `domain ${condition.match === "suffix" ? "ends with" : "="} ${condition.domain}`;
    case "sender_address":
      return `address = ${condition.address}`;
    case "header_condition": {
      const op = condition.op === "present" ? "present" : `${condition.op} "${condition.value}"`;
      return `${condition.header} ${op}`;
    }
    case "mime_type":
      return `mime = ${condition.type}`;
    default:
      return JSON.stringify(condition);
  }
}

function actionBadgeVariant(action: string): "default" | "secondary" | "destructive" | "outline" {
  if (action.startsWith("route_to:")) return "default";
  if (action === "skip") return "destructive";
  if (action === "metadata_only") return "secondary";
  return "outline";
}

// ---------------------------------------------------------------------------
// Condition field editors by rule_type
// ---------------------------------------------------------------------------

interface SenderDomainConditionProps {
  condition: Record<string, unknown>;
  onChange: (c: Record<string, unknown>) => void;
}

function SenderDomainCondition({ condition, onChange }: SenderDomainConditionProps) {
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label htmlFor="domain-input">Domain</Label>
        <Input
          id="domain-input"
          placeholder="e.g. chase.com"
          value={String(condition.domain ?? "")}
          onChange={(e) => onChange({ ...condition, domain: e.target.value.toLowerCase() })}
          data-testid="condition-domain"
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor="match-select">Match type</Label>
        <select
          id="match-select"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          value={String(condition.match ?? "exact")}
          onChange={(e) => onChange({ ...condition, match: e.target.value })}
          data-testid="condition-domain-match"
        >
          <option value="exact">Exact</option>
          <option value="suffix">Suffix (includes subdomains)</option>
        </select>
      </div>
    </div>
  );
}

interface SenderAddressConditionProps {
  condition: Record<string, unknown>;
  onChange: (c: Record<string, unknown>) => void;
}

function SenderAddressCondition({ condition, onChange }: SenderAddressConditionProps) {
  return (
    <div className="space-y-1">
      <Label htmlFor="address-input">Email address</Label>
      <Input
        id="address-input"
        placeholder="e.g. alerts@chase.com"
        value={String(condition.address ?? "")}
        onChange={(e) => onChange({ ...condition, address: e.target.value.toLowerCase() })}
        data-testid="condition-address"
      />
    </div>
  );
}

interface HeaderConditionProps {
  condition: Record<string, unknown>;
  onChange: (c: Record<string, unknown>) => void;
}

function HeaderCondition({ condition, onChange }: HeaderConditionProps) {
  const op = String(condition.op ?? "present");
  const needsValue = op === "equals" || op === "contains";

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label htmlFor="header-input">Header name</Label>
        <Input
          id="header-input"
          placeholder="e.g. List-Unsubscribe"
          value={String(condition.header ?? "")}
          onChange={(e) => onChange({ ...condition, header: e.target.value })}
          data-testid="condition-header"
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor="op-select">Operator</Label>
        <select
          id="op-select"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          value={op}
          onChange={(e) => {
            const newOp = e.target.value;
            onChange({
              ...condition,
              op: newOp,
              value: newOp === "present" ? null : (condition.value ?? ""),
            });
          }}
          data-testid="condition-header-op"
        >
          {HEADER_OPS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      {needsValue && (
        <div className="space-y-1">
          <Label htmlFor="header-value-input">Value</Label>
          <Input
            id="header-value-input"
            placeholder={op === "equals" ? "exact value" : "substring to match"}
            value={String(condition.value ?? "")}
            onChange={(e) => onChange({ ...condition, value: e.target.value })}
            data-testid="condition-header-value"
          />
        </div>
      )}
    </div>
  );
}

interface MimeTypeConditionProps {
  condition: Record<string, unknown>;
  onChange: (c: Record<string, unknown>) => void;
}

function MimeTypeCondition({ condition, onChange }: MimeTypeConditionProps) {
  return (
    <div className="space-y-1">
      <Label htmlFor="mime-input">MIME type</Label>
      <Input
        id="mime-input"
        placeholder="e.g. text/calendar or image/*"
        value={String(condition.type ?? "")}
        onChange={(e) => onChange({ ...condition, type: e.target.value.toLowerCase() })}
        data-testid="condition-mime"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Rule editor drawer
// ---------------------------------------------------------------------------

interface RuleEditorDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** If provided, the drawer edits this rule; otherwise creates a new one. */
  editRule: TriageRule | null;
}

function defaultConditionForType(ruleType: TriageRuleType): Record<string, unknown> {
  switch (ruleType) {
    case "sender_domain":
      return { domain: "", match: "exact" };
    case "sender_address":
      return { address: "" };
    case "header_condition":
      return { header: "", op: "present", value: null };
    case "mime_type":
      return { type: "" };
  }
}

function RuleEditorDrawer({ open, onOpenChange, editRule }: RuleEditorDrawerProps) {
  const isEditing = editRule !== null;

  const [ruleType, setRuleType] = useState<TriageRuleType>(
    (editRule?.rule_type as TriageRuleType) ?? "sender_domain",
  );
  const [condition, setCondition] = useState<Record<string, unknown>>(
    editRule?.condition ?? defaultConditionForType("sender_domain"),
  );
  const [action, setAction] = useState<string>(editRule?.action ?? "skip");
  const [routeTarget, setRouteTarget] = useState<string>(
    editRule?.action.startsWith("route_to:")
      ? editRule.action.slice("route_to:".length)
      : "finance",
  );
  const [isRouteAction, setIsRouteAction] = useState(
    editRule?.action.startsWith("route_to:") ?? false,
  );
  const [priority, setPriority] = useState<number>(editRule?.priority ?? 100);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{
    matched: boolean;
    reason: string;
    decision?: string | null;
    target_butler?: string | null;
  } | null>(null);
  const [testSender, setTestSender] = useState("");

  const createRule = useCreateTriageRule();
  const updateRule = useUpdateTriageRule();
  const testRule = useTestTriageRule();

  const isSaving = createRule.isPending || updateRule.isPending;
  const isTesting = testRule.isPending;

  function handleRuleTypeChange(newType: TriageRuleType) {
    setRuleType(newType);
    setCondition(defaultConditionForType(newType));
    setTestResult(null);
  }

  function resolvedAction(): string {
    return isRouteAction ? `route_to:${routeTarget}` : action;
  }

  function buildRuleCreate(): TriageRuleCreate {
    return {
      rule_type: ruleType,
      condition,
      action: resolvedAction(),
      priority,
      enabled: true,
    };
  }

  async function handleSave() {
    setError(null);
    const ruleData = buildRuleCreate();

    // Basic validation
    if (ruleType === "sender_domain" && !String(condition.domain ?? "").trim()) {
      setError("Domain is required.");
      return;
    }
    if (ruleType === "sender_address" && !String(condition.address ?? "").trim()) {
      setError("Email address is required.");
      return;
    }
    if (ruleType === "header_condition" && !String(condition.header ?? "").trim()) {
      setError("Header name is required.");
      return;
    }
    if (ruleType === "mime_type" && !String(condition.type ?? "").trim()) {
      setError("MIME type is required.");
      return;
    }
    if (isRouteAction && !routeTarget.trim()) {
      setError("Route target butler is required.");
      return;
    }

    try {
      if (isEditing && editRule) {
        await updateRule.mutateAsync({
          id: editRule.id,
          body: {
            condition: ruleData.condition,
            action: ruleData.action,
            priority: ruleData.priority,
          },
        });
      } else {
        await createRule.mutateAsync(ruleData);
      }
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save rule.");
    }
  }

  async function handleTest() {
    setError(null);
    setTestResult(null);
    if (!testSender.trim()) {
      setError("Enter a sender address or domain to test against.");
      return;
    }
    try {
      const result = await testRule.mutateAsync({
        envelope: { sender: { identity: testSender.trim() } },
        rule: buildRuleCreate(),
      });
      setTestResult(result.data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Test failed.");
    }
  }

  function handleClose(open: boolean) {
    if (!open) {
      setError(null);
      setTestResult(null);
      setTestSender("");
    }
    onOpenChange(open);
  }

  return (
    <Sheet open={open} onOpenChange={handleClose}>
      <SheetContent side="right" className="w-full sm:max-w-lg overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{isEditing ? "Edit Rule" : "New Rule"}</SheetTitle>
          <SheetDescription>
            {isEditing
              ? "Modify the triage rule settings."
              : "Create a deterministic triage rule for email routing."}
          </SheetDescription>
        </SheetHeader>

        <div className="px-4 space-y-5 pb-4">
          {/* Rule type selector */}
          <div className="space-y-1">
            <Label htmlFor="rule-type-select">Rule type</Label>
            <select
              id="rule-type-select"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={ruleType}
              onChange={(e) => handleRuleTypeChange(e.target.value as TriageRuleType)}
              data-testid="rule-type-select"
            >
              {RULE_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>

          {/* Condition fields (type-specific) */}
          <div className="rounded-md border border-muted p-3 space-y-3">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
              Condition
            </p>
            {ruleType === "sender_domain" && (
              <SenderDomainCondition condition={condition} onChange={setCondition} />
            )}
            {ruleType === "sender_address" && (
              <SenderAddressCondition condition={condition} onChange={setCondition} />
            )}
            {ruleType === "header_condition" && (
              <HeaderCondition condition={condition} onChange={setCondition} />
            )}
            {ruleType === "mime_type" && (
              <MimeTypeCondition condition={condition} onChange={setCondition} />
            )}
          </div>

          {/* Action selector */}
          <div className="space-y-2">
            <Label>Action</Label>
            <div className="flex items-center gap-2 mb-2">
              <input
                type="checkbox"
                id="route-action-toggle"
                checked={isRouteAction}
                onChange={(e) => {
                  setIsRouteAction(e.target.checked);
                  if (!e.target.checked) setAction("skip");
                }}
                className="h-4 w-4"
                data-testid="route-action-toggle"
              />
              <Label htmlFor="route-action-toggle" className="font-normal cursor-pointer">
                Route to butler
              </Label>
            </div>
            {isRouteAction ? (
              <select
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={routeTarget}
                onChange={(e) => setRouteTarget(e.target.value)}
                data-testid="route-target-select"
              >
                {AVAILABLE_BUTLERS.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
            ) : (
              <select
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={action}
                onChange={(e) => setAction(e.target.value)}
                data-testid="action-select"
              >
                {STATIC_ACTIONS.map((a) => (
                  <option key={a.value} value={a.value}>
                    {a.label}
                  </option>
                ))}
              </select>
            )}
          </div>

          {/* Priority */}
          <div className="space-y-1">
            <Label htmlFor="priority-input">Priority (lower = higher priority)</Label>
            <Input
              id="priority-input"
              type="number"
              min={0}
              value={priority}
              onChange={(e) => setPriority(Math.max(0, parseInt(e.target.value, 10) || 0))}
              data-testid="priority-input"
            />
          </div>

          {/* Test dry-run */}
          <div className="rounded-md border border-muted p-3 space-y-3">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
              Test rule (dry run)
            </p>
            <div className="space-y-1">
              <Label htmlFor="test-sender">Sender identity to test</Label>
              <Input
                id="test-sender"
                placeholder="e.g. alerts@chase.com"
                value={testSender}
                onChange={(e) => setTestSender(e.target.value)}
                data-testid="test-sender-input"
              />
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleTest}
              disabled={isTesting}
              data-testid="test-rule-btn"
            >
              {isTesting ? (
                <Loader2 className="mr-2 h-3 w-3 animate-spin" />
              ) : (
                <FlaskConical className="mr-2 h-3 w-3" />
              )}
              Test
            </Button>
            {testResult && (
              <div
                className={`flex items-start gap-2 rounded-md px-3 py-2 text-sm ${
                  testResult.matched
                    ? "bg-green-50 text-green-800 dark:bg-green-950 dark:text-green-200"
                    : "bg-muted text-muted-foreground"
                }`}
                data-testid="test-result"
              >
                {testResult.matched ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green-600 dark:text-green-400" />
                ) : (
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                )}
                <div>
                  <p className="font-medium">{testResult.matched ? "Matched" : "No match"}</p>
                  <p className="text-xs mt-0.5">{testResult.reason}</p>
                  {testResult.matched && testResult.decision && (
                    <p className="text-xs mt-0.5">
                      Decision: <span className="font-mono">{testResult.decision}</span>
                      {testResult.target_butler && ` → ${testResult.target_butler}`}
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>

          {error && (
            <div
              className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
              data-testid="editor-error"
            >
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              {error}
            </div>
          )}
        </div>

        <SheetFooter className="px-4">
          <Button type="button" variant="outline" onClick={() => handleClose(false)}>
            Cancel
          </Button>
          <Button type="button" onClick={handleSave} disabled={isSaving} data-testid="save-rule-btn">
            {isSaving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {isEditing ? "Save changes" : "Create rule"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------
// Import defaults dialog
// ---------------------------------------------------------------------------

interface ImportDefaultsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function ImportDefaultsDialog({ open, onOpenChange }: ImportDefaultsDialogProps) {
  const createRule = useCreateTriageRule();
  const [importing, setImporting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleImport() {
    setImporting(true);
    setError(null);
    try {
      for (const rule of SEED_RULES) {
        await createRule.mutateAsync(rule);
      }
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed.");
    } finally {
      setImporting(false);
    }
  }

  function handleClose(open: boolean) {
    if (!open) {
      setDone(false);
      setError(null);
    }
    onOpenChange(open);
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Import default rules</DialogTitle>
          <DialogDescription>
            The following seed rules will be created. Existing rules are not affected.
          </DialogDescription>
        </DialogHeader>

        {done ? (
          <div className="flex items-center gap-2 text-sm text-green-700 dark:text-green-300 py-2">
            <CheckCircle2 className="h-4 w-4 shrink-0" />
            {SEED_RULES.length} default rules imported successfully.
          </div>
        ) : (
          <div className="max-h-64 overflow-y-auto rounded-md border text-xs">
            <table className="w-full">
              <thead className="bg-muted/50">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Pri</th>
                  <th className="px-3 py-2 text-left font-medium">Type</th>
                  <th className="px-3 py-2 text-left font-medium">Condition</th>
                  <th className="px-3 py-2 text-left font-medium">Action</th>
                </tr>
              </thead>
              <tbody>
                {SEED_RULES.map((r, i) => (
                  <tr key={i} className="border-t">
                    <td className="px-3 py-1.5 tabular-nums">{r.priority}</td>
                    <td className="px-3 py-1.5 font-mono">{r.rule_type}</td>
                    <td className="px-3 py-1.5 font-mono">
                      {formatCondition(r.rule_type, r.condition as Record<string, unknown>)}
                    </td>
                    <td className="px-3 py-1.5">{formatAction(r.action)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {error && (
          <div className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </div>
        )}

        <DialogFooter>
          {done ? (
            <Button onClick={() => handleClose(false)}>Close</Button>
          ) : (
            <>
              <Button type="button" variant="outline" onClick={() => handleClose(false)}>
                Cancel
              </Button>
              <Button
                type="button"
                onClick={handleImport}
                disabled={importing}
                data-testid="confirm-import-btn"
              >
                {importing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Import {SEED_RULES.length} rules
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Delete confirmation dialog
// ---------------------------------------------------------------------------

interface DeleteConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  rule: TriageRule | null;
  onConfirm: (id: string) => Promise<void>;
}

function DeleteConfirmDialog({ open, onOpenChange, rule, onConfirm }: DeleteConfirmDialogProps) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConfirm() {
    if (!rule) return;
    setDeleting(true);
    setError(null);
    try {
      await onConfirm(rule.id);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed.");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Delete rule?</DialogTitle>
          <DialogDescription>
            {rule
              ? `Priority ${rule.priority} — ${formatCondition(rule.rule_type as TriageRuleType, rule.condition as Record<string, unknown>)}`
              : ""}
            <br />
            This action cannot be undone.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <div className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleConfirm}
            disabled={deleting}
            data-testid="confirm-delete-btn"
          >
            {deleting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Rules table
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 4, cols = 7 }: { count?: number; cols?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          {Array.from({ length: cols }, (_, j) => (
            <TableCell key={j}>
              <Skeleton className="h-4 w-full" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}

interface RulesTableProps {
  onEdit: (rule: TriageRule) => void;
  onDelete: (rule: TriageRule) => void;
  onImportDefaults: () => void;
  onNew: () => void;
}

function RulesTable({ onEdit, onDelete, onImportDefaults, onNew }: RulesTableProps) {
  const { data, isLoading, error } = useTriageRules();
  const updateRule = useUpdateTriageRule();

  const rules = data?.data ?? [];

  function handleToggleEnabled(rule: TriageRule) {
    updateRule.mutate({ id: rule.id, body: { enabled: !rule.enabled } });
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-destructive" data-testid="rules-error">
        <AlertCircle className="h-4 w-4" />
        Failed to load triage rules. Check your connection and try again.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">Rules</h3>
          {!isLoading && (
            <p className="text-xs text-muted-foreground">
              {rules.length > 0
                ? `${rules.length} rule${rules.length !== 1 ? "s" : ""}, sorted by priority`
                : "No rules yet"}
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={onImportDefaults}
            data-testid="import-defaults-btn"
          >
            Import defaults
          </Button>
          <Button size="sm" onClick={onNew} data-testid="new-rule-btn">
            <Plus className="mr-1 h-4 w-4" />
            New
          </Button>
        </div>
      </div>

      <Table data-testid="rules-table">
        <TableHeader>
          <TableRow>
            <TableHead className="w-16">Priority</TableHead>
            <TableHead>Condition</TableHead>
            <TableHead>Action</TableHead>
            <TableHead className="text-center">Enabled</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {isLoading ? (
            <SkeletonRows />
          ) : rules.length === 0 ? (
            <TableRow>
              <TableCell colSpan={5} className="py-12 text-center text-sm text-muted-foreground">
                No triage rules yet. Click{" "}
                <button
                  className="underline hover:no-underline"
                  onClick={onImportDefaults}
                  data-testid="empty-import-defaults-link"
                >
                  Import defaults
                </button>{" "}
                to get started, or{" "}
                <button className="underline hover:no-underline" onClick={onNew}>
                  create a rule manually
                </button>
                .
              </TableCell>
            </TableRow>
          ) : (
            rules.map((rule) => (
              <TableRow
                key={rule.id}
                className="cursor-pointer hover:bg-muted/50"
                onClick={() => onEdit(rule)}
                data-testid={`rule-row-${rule.id}`}
              >
                <TableCell className="tabular-nums font-medium">{rule.priority}</TableCell>
                <TableCell className="text-sm font-mono text-muted-foreground">
                  {formatCondition(rule.rule_type as TriageRuleType, rule.condition as Record<string, unknown>)}
                </TableCell>
                <TableCell>
                  <Badge variant={actionBadgeVariant(rule.action)}>{formatAction(rule.action)}</Badge>
                </TableCell>
                <TableCell className="text-center">
                  <button
                    className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 ${
                      rule.enabled ? "bg-primary" : "bg-input"
                    }`}
                    role="switch"
                    aria-checked={rule.enabled}
                    aria-label={rule.enabled ? "Disable rule" : "Enable rule"}
                    onClick={(e) => {
                      e.stopPropagation();
                      handleToggleEnabled(rule);
                    }}
                    data-testid={`toggle-enabled-${rule.id}`}
                  >
                    <span
                      className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform ${
                        rule.enabled ? "translate-x-4" : "translate-x-0.5"
                      }`}
                    />
                  </button>
                </TableCell>
                <TableCell className="text-right">
                  <div
                    className="flex items-center justify-end gap-1"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onEdit(rule)}
                      aria-label="Edit rule"
                      data-testid={`edit-rule-${rule.id}`}
                    >
                      <Edit2 className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onDelete(rule)}
                      aria-label="Delete rule"
                      data-testid={`delete-rule-${rule.id}`}
                    >
                      <Trash2 className="h-3.5 w-3.5 text-destructive" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Thread affinity panel
// ---------------------------------------------------------------------------

function ThreadAffinityPanel() {
  const { data: settings, isLoading } = useThreadAffinitySettings();
  const updateSettings = useUpdateThreadAffinitySettings();

  const [ttlInput, setTtlInput] = useState<string>("");
  const [ttlEditing, setTtlEditing] = useState(false);

  const enabled = settings?.enabled ?? true;
  const ttlDays = settings?.ttl_days ?? 30;

  function handleToggle() {
    updateSettings.mutate({ enabled: !enabled });
  }

  function handleTtlSave() {
    const val = parseInt(ttlInput, 10);
    if (!Number.isNaN(val) && val > 0) {
      updateSettings.mutate({ ttl_days: val });
    }
    setTtlEditing(false);
  }

  if (isLoading) {
    return (
      <div className="space-y-2 py-2">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-4 w-32" />
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3" data-testid="thread-affinity-panel">
      {/* Toggle */}
      <div className="flex items-center gap-2">
        <button
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 ${
            enabled ? "bg-primary" : "bg-input"
          }`}
          role="switch"
          aria-checked={enabled}
          aria-label={enabled ? "Disable thread affinity" : "Enable thread affinity"}
          onClick={handleToggle}
          data-testid="thread-affinity-toggle"
        >
          <span
            className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform ${
              enabled ? "translate-x-4" : "translate-x-0.5"
            }`}
          />
        </button>
        <span className="text-sm">
          Thread affinity:{" "}
          <span className={`font-medium ${enabled ? "text-primary" : "text-muted-foreground"}`}>
            {enabled ? "ON" : "OFF"}
          </span>
        </span>
      </div>

      {/* TTL */}
      <div className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">TTL (days):</span>
        {ttlEditing ? (
          <>
            <Input
              className="h-7 w-20 text-sm"
              type="number"
              min={1}
              value={ttlInput}
              onChange={(e) => setTtlInput(e.target.value)}
              autoFocus
              data-testid="ttl-input"
              onKeyDown={(e) => {
                if (e.key === "Enter") handleTtlSave();
                if (e.key === "Escape") setTtlEditing(false);
              }}
            />
            <Button size="sm" variant="outline" onClick={handleTtlSave}>
              Save
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setTtlEditing(false)}
            >
              Cancel
            </Button>
          </>
        ) : (
          <button
            className="text-sm font-medium underline decoration-dashed underline-offset-2 hover:no-underline"
            onClick={() => {
              setTtlInput(String(ttlDays));
              setTtlEditing(true);
            }}
            data-testid="ttl-display"
          >
            {ttlDays}
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Gmail label filters panel
// ---------------------------------------------------------------------------

interface TagInputProps {
  tags: string[];
  placeholder?: string;
  onAdd: (tag: string) => void;
  onRemove: (tag: string) => void;
  testId?: string;
}

function TagInput({ tags, placeholder, onAdd, onRemove, testId }: TagInputProps) {
  const [value, setValue] = useState("");

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if ((e.key === "Enter" || e.key === ",") && value.trim()) {
      e.preventDefault();
      onAdd(value.trim().toUpperCase());
      setValue("");
    }
    if (e.key === "Backspace" && !value && tags.length > 0) {
      onRemove(tags[tags.length - 1]);
    }
  }

  return (
    <div
      className="flex flex-wrap items-center gap-1.5 rounded-md border border-input bg-background p-2 min-h-[2.5rem] focus-within:ring-1 focus-within:ring-ring"
      data-testid={testId}
    >
      {tags.map((tag) => (
        <Badge key={tag} variant="secondary" className="gap-1 py-0.5">
          {tag}
          <button
            className="ml-0.5 rounded-sm opacity-60 hover:opacity-100"
            onClick={() => onRemove(tag)}
            aria-label={`Remove ${tag}`}
          >
            ×
          </button>
        </Badge>
      ))}
      <input
        className="flex-1 min-w-[6rem] bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        placeholder={tags.length === 0 ? placeholder : "Add label…"}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
      />
    </div>
  );
}

function GmailLabelFiltersPanel() {
  // Gmail label filters are stored in thread affinity settings extended data.
  // Per spec, include/exclude labels are a UI feature — this is a local state
  // placeholder wired to display until the backend extends settings for label storage.
  const [includeLabels, setIncludeLabels] = useState<string[]>(["INBOX", "IMPORTANT"]);
  const [excludeLabels, setExcludeLabels] = useState<string[]>(["PROMOTIONS", "SOCIAL"]);

  function addInclude(tag: string) {
    if (!includeLabels.includes(tag)) setIncludeLabels((p) => [...p, tag]);
  }
  function removeInclude(tag: string) {
    setIncludeLabels((p) => p.filter((t) => t !== tag));
  }
  function addExclude(tag: string) {
    if (!excludeLabels.includes(tag)) setExcludeLabels((p) => [...p, tag]);
  }
  function removeExclude(tag: string) {
    setExcludeLabels((p) => p.filter((t) => t !== tag));
  }

  return (
    <div className="space-y-4" data-testid="gmail-label-panel">
      <p className="text-xs text-amber-600 dark:text-amber-400">
        Changes are not yet persisted — this panel is a UI preview pending backend label-filter support.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <div className="space-y-1">
        <Label>Include labels</Label>
        <p className="text-xs text-muted-foreground">Only process emails with these labels.</p>
        <TagInput
          tags={includeLabels}
          placeholder="Type a label and press Enter…"
          onAdd={addInclude}
          onRemove={removeInclude}
          testId="include-labels-input"
        />
      </div>
      <div className="space-y-1">
        <Label>Exclude labels</Label>
        <p className="text-xs text-muted-foreground">Skip emails that have any of these labels.</p>
        <TagInput
          tags={excludeLabels}
          placeholder="Type a label and press Enter…"
          onAdd={addExclude}
          onRemove={removeExclude}
          testId="exclude-labels-input"
        />
      </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Email filters section
// ---------------------------------------------------------------------------

function EmailFiltersSection() {
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<TriageRule | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deletingRule, setDeletingRule] = useState<TriageRule | null>(null);

  const deleteRule = useDeleteTriageRule();

  function handleEdit(rule: TriageRule) {
    setEditingRule(rule);
    setEditorOpen(true);
  }

  function handleNew() {
    setEditingRule(null);
    setEditorOpen(true);
  }

  function handleDeleteRequest(rule: TriageRule) {
    setDeletingRule(rule);
    setDeleteOpen(true);
  }

  async function handleDeleteConfirm(id: string) {
    await deleteRule.mutateAsync(id);
  }

  function handleEditorClose(open: boolean) {
    setEditorOpen(open);
    if (!open) setEditingRule(null);
  }

  return (
    <div className="space-y-6" data-testid="email-filters-section">
      {/* Rules table */}
      <Card>
        <CardContent className="pt-4 p-4">
          <RulesTable
            onEdit={handleEdit}
            onDelete={handleDeleteRequest}
            onImportDefaults={() => setImportOpen(true)}
            onNew={handleNew}
          />
        </CardContent>
      </Card>

      {/* Thread affinity */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Thread Affinity</CardTitle>
          <CardDescription>
            Route follow-up emails in the same thread to the same butler without LLM
            re-classification.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ThreadAffinityPanel />
        </CardContent>
      </Card>

      {/* Gmail label filters */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Gmail Label Filters</CardTitle>
          <CardDescription>
            Control which Gmail labels are included or excluded from ingestion.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <GmailLabelFiltersPanel />
        </CardContent>
      </Card>

      {/* Drawers and dialogs */}
      <RuleEditorDrawer
        key={editingRule?.id ?? "new"}
        open={editorOpen}
        onOpenChange={handleEditorClose}
        editRule={editingRule}
      />
      <ImportDefaultsDialog open={importOpen} onOpenChange={setImportOpen} />
      <DeleteConfirmDialog
        open={deleteOpen}
        onOpenChange={(open) => {
          setDeleteOpen(open);
          if (!open) setDeletingRule(null);
        }}
        rule={deletingRule}
        onConfirm={handleDeleteConfirm}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// FiltersTab — top-level export
// ---------------------------------------------------------------------------

export function FiltersTab() {
  return (
    <div className="space-y-4" data-testid="filters-tab">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Filters</h2>
          <p className="text-sm text-muted-foreground">
            Deterministic ingestion policy — triage rules, thread affinity, and label filters.
          </p>
        </div>
      </div>

      <Tabs defaultValue="email">
        <TabsList>
          <TabsTrigger value="email">Email</TabsTrigger>
          <TabsTrigger value="telegram">Telegram</TabsTrigger>
        </TabsList>

        <TabsContent value="email" className="mt-4">
          <EmailFiltersSection />
        </TabsContent>

        <TabsContent value="telegram" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Telegram Filters</CardTitle>
              <CardDescription>
                Telegram filter controls will be available in a future update.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                This section is a placeholder for future Telegram-channel filter parity.
              </p>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
