/**
 * ConnectorRulesSection — inline ingestion rules table for a specific connector.
 *
 * Displays connector-scoped ingestion rules (scope=connector:<type>:<identity>)
 * on the ConnectorDetailPage. Replaces the old ConnectorFiltersDialog.
 *
 * Features:
 * - Rules table with priority, name, condition, action, enabled toggle, delete
 * - "+ Add Rule" button opens a rule editor drawer with pre-filled scope
 * - Uses the unified ingestion rules API (use-ingestion-rules hooks)
 * - Connector-scoped rules only support action=block (enforced by API)
 */

import { useState } from "react";
import { AlertCircle, Loader2, Plus, Shield, Trash2 } from "lucide-react";

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

import {
  useCreateIngestionRule,
  useDeleteIngestionRule,
  useIngestionRules,
  useUpdateIngestionRule,
} from "@/hooks/use-ingestion-rules";
import type { IngestionRule } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const RULE_TYPES: { value: string; label: string }[] = [
  { value: "sender_domain", label: "Sender Domain" },
  { value: "sender_address", label: "Sender Address" },
  { value: "header_condition", label: "Email Header" },
  { value: "mime_type", label: "MIME Attachment Type" },
  { value: "chat_id", label: "Chat ID" },
  { value: "source_channel", label: "Source Channel" },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatCondition(
  ruleType: string,
  condition: Record<string, unknown>,
): string {
  switch (ruleType) {
    case "sender_domain":
      return `domain ${condition.match === "suffix" ? "ends with" : "="} ${condition.domain}`;
    case "sender_address":
      return `address = ${condition.address}`;
    case "header_condition": {
      const op =
        condition.op === "present"
          ? "present"
          : `${condition.op} "${condition.value}"`;
      return `${condition.header} ${op}`;
    }
    case "mime_type":
      return `mime = ${condition.type}`;
    case "chat_id":
      return `chat_id = ${condition.chat_id}`;
    case "source_channel":
      return `channel = ${condition.channel}`;
    default:
      return JSON.stringify(condition);
  }
}

function defaultConditionForType(ruleType: string): Record<string, unknown> {
  switch (ruleType) {
    case "sender_domain":
      return { domain: "", match: "exact" };
    case "sender_address":
      return { address: "" };
    case "header_condition":
      return { header: "", op: "present", value: null };
    case "mime_type":
      return { type: "" };
    case "chat_id":
      return { chat_id: "" };
    case "source_channel":
      return { channel: "" };
    default:
      return {};
  }
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ConnectorRulesSectionProps {
  connectorType: string;
  endpointIdentity: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ConnectorRulesSection({
  connectorType,
  endpointIdentity,
}: ConnectorRulesSectionProps) {
  const scope = `connector:${connectorType}:${endpointIdentity}`;

  const { data, isLoading, error } = useIngestionRules({ scope });
  const updateRule = useUpdateIngestionRule();
  const deleteRule = useDeleteIngestionRule();

  const rules = data?.data ?? [];

  // Editor drawer state
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<IngestionRule | null>(null);

  // Delete confirmation state
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deletingRule, setDeletingRule] = useState<IngestionRule | null>(null);

  function handleNew() {
    setEditingRule(null);
    setEditorOpen(true);
  }

  function handleEdit(rule: IngestionRule) {
    setEditingRule(rule);
    setEditorOpen(true);
  }

  function handleDeleteClick(rule: IngestionRule) {
    setDeletingRule(rule);
    setDeleteDialogOpen(true);
  }

  async function handleConfirmDelete(ruleId: string) {
    await deleteRule.mutateAsync(ruleId);
    setDeleteDialogOpen(false);
    setDeletingRule(null);
  }

  function handleToggleEnabled(rule: IngestionRule) {
    updateRule.mutate({ id: rule.id, body: { enabled: !rule.enabled } });
  }

  return (
    <Card data-testid="connector-rules-section">
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Shield className="h-4 w-4" />
              Ingestion Rules
            </CardTitle>
            <CardDescription>
              Block rules evaluated before data enters the system.
              {!isLoading && rules.length > 0 && (
                <span className="ml-1">
                  {rules.length} rule{rules.length !== 1 ? "s" : ""}
                </span>
              )}
            </CardDescription>
          </div>
          <Button
            size="sm"
            onClick={handleNew}
            data-testid="add-rule-btn"
          >
            <Plus className="mr-1 h-4 w-4" />
            Add Rule
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {error ? (
          <div
            className="flex items-center gap-2 text-sm text-destructive"
            data-testid="rules-error"
          >
            <AlertCircle className="h-4 w-4" />
            Failed to load ingestion rules.
          </div>
        ) : (
          <Table data-testid="connector-rules-table">
            <TableHeader>
              <TableRow>
                <TableHead className="w-16">Priority</TableHead>
                <TableHead>Rule</TableHead>
                <TableHead>Condition</TableHead>
                <TableHead>Action</TableHead>
                <TableHead className="text-center">Enabled</TableHead>
                <TableHead className="w-12" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <SkeletonRows />
              ) : rules.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="py-8 text-center text-sm text-muted-foreground"
                  >
                    No block rules for this connector.{" "}
                    <button
                      className="underline hover:no-underline"
                      onClick={handleNew}
                    >
                      Add one
                    </button>{" "}
                    to filter incoming messages.
                  </TableCell>
                </TableRow>
              ) : (
                rules.map((rule) => (
                  <TableRow
                    key={rule.id}
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => handleEdit(rule)}
                    data-testid={`rule-row-${rule.id}`}
                  >
                    <TableCell className="tabular-nums font-medium">
                      {rule.priority}
                    </TableCell>
                    <TableCell className="text-sm">
                      {rule.name ?? (
                        <span className="text-muted-foreground italic">
                          unnamed
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-sm font-mono text-muted-foreground">
                      {formatCondition(rule.rule_type, rule.condition)}
                    </TableCell>
                    <TableCell>
                      <Badge variant="destructive" className="text-xs">
                        {rule.action}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-center">
                      <button
                        className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 ${
                          rule.enabled ? "bg-primary" : "bg-input"
                        }`}
                        role="switch"
                        aria-checked={rule.enabled}
                        aria-label={
                          rule.enabled ? "Disable rule" : "Enable rule"
                        }
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
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 p-0"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteClick(rule);
                        }}
                        data-testid={`delete-rule-${rule.id}`}
                      >
                        <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
                        <span className="sr-only">Delete rule</span>
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        )}
      </CardContent>

      {/* Rule editor drawer */}
      <ConnectorRuleEditor
        open={editorOpen}
        onOpenChange={setEditorOpen}
        editRule={editingRule}
        scope={scope}
        connectorType={connectorType}
        endpointIdentity={endpointIdentity}
      />

      {/* Delete confirmation */}
      <DeleteRuleDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        rule={deletingRule}
        onConfirm={handleConfirmDelete}
      />
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Skeleton rows
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 3 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          {Array.from({ length: 6 }, (_, j) => (
            <TableCell key={j}>
              <Skeleton className="h-4 w-full" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// Condition editors
// ---------------------------------------------------------------------------

interface ConditionEditorProps {
  ruleType: string;
  condition: Record<string, unknown>;
  onChange: (c: Record<string, unknown>) => void;
}

function ConditionEditor({ ruleType, condition, onChange }: ConditionEditorProps) {
  switch (ruleType) {
    case "sender_domain":
      return (
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="domain-input">Domain</Label>
            <Input
              id="domain-input"
              placeholder="e.g. noreply.example.com"
              value={String(condition.domain ?? "")}
              onChange={(e) =>
                onChange({ ...condition, domain: e.target.value.toLowerCase() })
              }
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

    case "sender_address":
      return (
        <div className="space-y-1">
          <Label htmlFor="address-input">Email address</Label>
          <Input
            id="address-input"
            placeholder="e.g. alerts@example.com"
            value={String(condition.address ?? "")}
            onChange={(e) =>
              onChange({ ...condition, address: e.target.value.toLowerCase() })
            }
            data-testid="condition-address"
          />
        </div>
      );

    case "header_condition": {
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
              onChange={(e) =>
                onChange({ ...condition, header: e.target.value })
              }
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
              <option value="present">is present</option>
              <option value="equals">equals</option>
              <option value="contains">contains</option>
            </select>
          </div>
          {needsValue && (
            <div className="space-y-1">
              <Label htmlFor="header-value-input">Value</Label>
              <Input
                id="header-value-input"
                placeholder={
                  op === "equals" ? "exact value" : "substring to match"
                }
                value={String(condition.value ?? "")}
                onChange={(e) =>
                  onChange({ ...condition, value: e.target.value })
                }
                data-testid="condition-header-value"
              />
            </div>
          )}
        </div>
      );
    }

    case "mime_type":
      return (
        <div className="space-y-1">
          <Label htmlFor="mime-input">MIME type</Label>
          <Input
            id="mime-input"
            placeholder="e.g. text/calendar or image/*"
            value={String(condition.type ?? "")}
            onChange={(e) =>
              onChange({ ...condition, type: e.target.value.toLowerCase() })
            }
            data-testid="condition-mime"
          />
        </div>
      );

    case "chat_id":
      return (
        <div className="space-y-1">
          <Label htmlFor="chat-id-input">Chat ID</Label>
          <Input
            id="chat-id-input"
            placeholder="e.g. 123456789"
            value={String(condition.chat_id ?? "")}
            onChange={(e) =>
              onChange({ ...condition, chat_id: e.target.value })
            }
            data-testid="condition-chat-id"
          />
        </div>
      );

    case "source_channel":
      return (
        <div className="space-y-1">
          <Label htmlFor="channel-input">Channel</Label>
          <Input
            id="channel-input"
            placeholder="e.g. telegram, gmail"
            value={String(condition.channel ?? "")}
            onChange={(e) =>
              onChange({ ...condition, channel: e.target.value.toLowerCase() })
            }
            data-testid="condition-channel"
          />
        </div>
      );

    default:
      return (
        <div className="space-y-1">
          <Label htmlFor="raw-condition">Condition (JSON)</Label>
          <Input
            id="raw-condition"
            value={JSON.stringify(condition)}
            onChange={(e) => {
              try {
                onChange(JSON.parse(e.target.value));
              } catch {
                // ignore invalid JSON while typing
              }
            }}
            data-testid="condition-raw"
          />
        </div>
      );
  }
}

// ---------------------------------------------------------------------------
// Rule editor drawer (connector-scoped, action always "block")
// ---------------------------------------------------------------------------

interface ConnectorRuleEditorProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editRule: IngestionRule | null;
  scope: string;
  connectorType: string;
  endpointIdentity: string;
}

function ConnectorRuleEditor({
  open,
  onOpenChange,
  editRule,
  scope,
  connectorType,
  endpointIdentity,
}: ConnectorRuleEditorProps) {
  const isEditing = editRule !== null;

  const [ruleType, setRuleType] = useState<string>(
    editRule?.rule_type ?? "sender_domain",
  );
  const [condition, setCondition] = useState<Record<string, unknown>>(
    editRule?.condition ?? defaultConditionForType("sender_domain"),
  );
  const [priority, setPriority] = useState<number>(editRule?.priority ?? 100);
  const [name, setName] = useState<string>(editRule?.name ?? "");
  const [description, setDescription] = useState<string>(
    editRule?.description ?? "",
  );
  const [error, setError] = useState<string | null>(null);

  const createRule = useCreateIngestionRule();
  const updateRule = useUpdateIngestionRule();

  const isSaving = createRule.isPending || updateRule.isPending;

  // Reset form when editRule changes or drawer opens
  // We use the open prop as a signal to sync state
  if (open && editRule && ruleType !== editRule.rule_type) {
    setRuleType(editRule.rule_type);
    setCondition(editRule.condition);
    setPriority(editRule.priority);
    setName(editRule.name ?? "");
    setDescription(editRule.description ?? "");
    setError(null);
  }

  function handleRuleTypeChange(newType: string) {
    setRuleType(newType);
    setCondition(defaultConditionForType(newType));
  }

  // Filter rule types based on connector type
  const availableRuleTypes = RULE_TYPES.filter((rt) => {
    // chat_id only valid for telegram-bot connectors
    if (rt.value === "chat_id" && !connectorType.startsWith("telegram")) {
      return false;
    }
    return true;
  });

  async function handleSave() {
    setError(null);

    // Basic validation
    if (ruleType === "sender_domain" && !String(condition.domain ?? "").trim()) {
      setError("Domain is required.");
      return;
    }
    if (
      ruleType === "sender_address" &&
      !String(condition.address ?? "").trim()
    ) {
      setError("Email address is required.");
      return;
    }
    if (
      ruleType === "header_condition" &&
      !String(condition.header ?? "").trim()
    ) {
      setError("Header name is required.");
      return;
    }
    if (ruleType === "mime_type" && !String(condition.type ?? "").trim()) {
      setError("MIME type is required.");
      return;
    }
    if (ruleType === "chat_id" && !String(condition.chat_id ?? "").trim()) {
      setError("Chat ID is required.");
      return;
    }
    if (
      ruleType === "source_channel" &&
      !String(condition.channel ?? "").trim()
    ) {
      setError("Channel is required.");
      return;
    }

    try {
      if (isEditing && editRule) {
        await updateRule.mutateAsync({
          id: editRule.id,
          body: {
            condition,
            priority,
            name: name.trim() || null,
            description: description.trim() || null,
          },
        });
      } else {
        await createRule.mutateAsync({
          scope,
          rule_type: ruleType,
          condition,
          action: "block",
          priority,
          name: name.trim() || null,
          description: description.trim() || null,
        });
      }
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save rule.");
    }
  }

  function handleClose(isOpen: boolean) {
    if (!isOpen) {
      setError(null);
      setRuleType("sender_domain");
      setCondition(defaultConditionForType("sender_domain"));
      setPriority(100);
      setName("");
      setDescription("");
    }
    onOpenChange(isOpen);
  }

  return (
    <Sheet open={open} onOpenChange={handleClose}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-lg overflow-y-auto"
      >
        <SheetHeader>
          <SheetTitle>
            {isEditing ? "Edit Block Rule" : "New Block Rule"}
          </SheetTitle>
          <SheetDescription>
            {isEditing
              ? "Modify the block rule for this connector."
              : "Create a block rule to filter incoming messages before ingestion."}
          </SheetDescription>
        </SheetHeader>

        <div className="px-4 space-y-5 pb-4">
          {/* Scope indicator */}
          <div className="rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
            Scope:{" "}
            <span className="font-mono font-medium text-foreground">
              {connectorType}:{endpointIdentity}
            </span>
          </div>

          {/* Name */}
          <div className="space-y-1">
            <Label htmlFor="rule-name-input">Name (optional)</Label>
            <Input
              id="rule-name-input"
              placeholder="e.g. Block marketing emails"
              value={name}
              onChange={(e) => setName(e.target.value)}
              data-testid="rule-name-input"
            />
          </div>

          {/* Rule type selector */}
          <div className="space-y-1">
            <Label htmlFor="rule-type-select">Rule type</Label>
            <select
              id="rule-type-select"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={ruleType}
              onChange={(e) => handleRuleTypeChange(e.target.value)}
              disabled={isEditing}
              data-testid="rule-type-select"
            >
              {availableRuleTypes.map((t) => (
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
            <ConditionEditor
              ruleType={ruleType}
              condition={condition}
              onChange={setCondition}
            />
          </div>

          {/* Action (fixed to block for connector-scoped) */}
          <div className="space-y-1">
            <Label>Action</Label>
            <div className="flex items-center gap-2">
              <Badge variant="destructive">block</Badge>
              <span className="text-xs text-muted-foreground">
                Connector-scoped rules always block matching messages.
              </span>
            </div>
          </div>

          {/* Priority */}
          <div className="space-y-1">
            <Label htmlFor="priority-input">
              Priority (lower = higher priority)
            </Label>
            <Input
              id="priority-input"
              type="number"
              min={0}
              value={priority}
              onChange={(e) =>
                setPriority(Math.max(0, parseInt(e.target.value, 10) || 0))
              }
              data-testid="priority-input"
            />
          </div>

          {/* Description */}
          <div className="space-y-1">
            <Label htmlFor="rule-description-input">
              Description (optional)
            </Label>
            <Input
              id="rule-description-input"
              placeholder="Why this rule exists"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              data-testid="rule-description-input"
            />
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
          <Button
            type="button"
            variant="outline"
            onClick={() => handleClose(false)}
          >
            Cancel
          </Button>
          <Button
            type="button"
            onClick={handleSave}
            disabled={isSaving}
            data-testid="save-rule-btn"
          >
            {isSaving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {isEditing ? "Save changes" : "Create rule"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------
// Delete confirmation dialog
// ---------------------------------------------------------------------------

interface DeleteRuleDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  rule: IngestionRule | null;
  onConfirm: (id: string) => Promise<void>;
}

function DeleteRuleDialog({
  open,
  onOpenChange,
  rule,
  onConfirm,
}: DeleteRuleDialogProps) {
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
          <DialogTitle>Delete block rule?</DialogTitle>
          <DialogDescription>
            {rule ? (
              <>
                Priority {rule.priority}
                {rule.name ? ` — ${rule.name}` : ""} —{" "}
                {formatCondition(rule.rule_type, rule.condition)}
              </>
            ) : (
              ""
            )}
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
