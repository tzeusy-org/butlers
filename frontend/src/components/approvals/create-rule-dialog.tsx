import { useDraft } from "@/hooks/use-draft";
import { DraftNotice } from "@/components/ui/draft-notice";
import { useState } from "react";
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
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Textarea } from "@/components/ui/textarea";
import { useCreateRule } from "@/hooks/use-approvals";
import { useButlers } from "@/hooks/use-butlers";

const GATED_TOOLS = [
  "notify",
  "telegram_send_message",
  "email_send_message",
] as const;

interface CreateRuleDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const EMPTY_FORM = {
  butler: "",
  tool_name: "",
  description: "",
  arg_constraints_text: "{}",
  max_uses: "",
  expires_days: "",
};

export function CreateRuleDialog({ open, onOpenChange }: CreateRuleDialogProps) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [constraintsError, setConstraintsError] = useState<string | null>(null);
  const draft = useDraft("approvals.rule.create", ["create"], { tool_name: form.tool_name, description: form.description, arg_constraints_text: form.arg_constraints_text, max_uses: form.max_uses, expires_days: form.expires_days }, (v) => setForm((current) => ({ ...current, ...v })), open, { tool_name: EMPTY_FORM.tool_name, description: EMPTY_FORM.description, arg_constraints_text: EMPTY_FORM.arg_constraints_text, max_uses: EMPTY_FORM.max_uses, expires_days: EMPTY_FORM.expires_days });
  const createMutation = useCreateRule();
  const butlersQuery = useButlers();
  const butlerNames = butlersQuery.data
    ? butlersQuery.data.data.map((b) => b.name).sort()
    : [];

  function resetForm() {
    setForm(EMPTY_FORM);
    setConstraintsError(null);
  }

  async function handleSubmit() {
    let argConstraints: Record<string, unknown>;
    try {
      argConstraints = JSON.parse(form.arg_constraints_text);
    } catch {
      setConstraintsError("Invalid JSON");
      return;
    }
    setConstraintsError(null);

    const expiresAt = form.expires_days
      ? new Date(Date.now() + Number(form.expires_days) * 86400000).toISOString()
      : undefined;

    // Built as a variable, not a literal: `butler` joins ApprovalRuleCreateRequest with
    // #4603, and a literal would fail the excess-property check on main before then.
    const payload = {
      butler: form.butler,
      tool_name: form.tool_name,
      description: form.description,
      arg_constraints: argConstraints,
      max_uses: form.max_uses ? Number(form.max_uses) : undefined,
      expires_at: expiresAt,
    };

    const submission = draft.capture();
    const preparation = draft.prepare();
    if (preparation instanceof Promise ? !await preparation : !preparation) return;
    createMutation.mutate(payload, {
      onSuccess: () => {
        void draft.submitted(submission, () => { resetForm(); onOpenChange(false); });
      },
      onSettled: draft.finish,
    });
  }

  const isValid =
    form.butler !== "" && form.tool_name.trim() !== "" && form.description.trim() !== "";

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) draft.dismiss(() => { resetForm(); onOpenChange(false); });
        else onOpenChange(v);
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Create standing rule</DialogTitle>
          <DialogDescription>
            Auto-approve matching tool calls without manual review.
          </DialogDescription>
        </DialogHeader>

        <DraftNotice session={draft.session} />
        <div className="space-y-4">
          <div>
            <Label htmlFor="butler">Butler</Label>
            <select
              id="butler"
              className="mt-1 flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
              value={form.butler}
              disabled={!butlersQuery.data}
              onChange={(e) => setForm((f) => ({ ...f, butler: e.target.value }))}
            >
              <option value="">Select a butler</option>
              {butlerNames.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            {butlersQuery.isLoading && (
              <p className="text-muted-foreground text-xs mt-1">Loading butlers</p>
            )}
            {butlersQuery.isError && (
              <SourceDegradedNote
                label="Butlers"
                onRetry={() => void butlersQuery.refetch()}
                className="mt-1"
              />
            )}
            <p className="text-muted-foreground text-xs mt-1">
              The butler whose approvals own this rule.
            </p>
          </div>

          <div>
            <Label htmlFor="tool_name">Tool Name</Label>
            <div className="flex gap-2 mt-1">
              <Input
                id="tool_name"
                placeholder="e.g. notify"
                value={form.tool_name}
                onChange={(e) => setForm((f) => ({ ...f, tool_name: e.target.value }))}
              />
            </div>
            <div className="flex gap-1 mt-1.5">
              {GATED_TOOLS.map((t) => (
                <Button
                  key={t}
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs text-muted-foreground"
                  onClick={() => setForm((f) => ({ ...f, tool_name: t }))}
                >
                  {t}
                </Button>
              ))}
            </div>
          </div>

          <div>
            <Label htmlFor="description">Description</Label>
            <Input
              id="description"
              placeholder="What this rule allows"
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              className="mt-1"
            />
          </div>

          <div>
            <Label htmlFor="constraints">
              Argument Constraints <span className="text-muted-foreground font-normal">(JSON)</span>
            </Label>
            <Textarea
              id="constraints"
              rows={4}
              className="mt-1 font-mono text-xs"
              value={form.arg_constraints_text}
              onChange={(e) => {
                setForm((f) => ({ ...f, arg_constraints_text: e.target.value }));
                setConstraintsError(null);
              }}
            />
            {constraintsError && (
              <p className="text-destructive text-xs mt-1">{constraintsError}</p>
            )}
            <p className="text-muted-foreground text-xs mt-1">
              Use <code>{`{"key": {"type": "exact", "value": "..."}}`}</code> or{" "}
              <code>{`{"type": "any"}`}</code> for wildcards.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label htmlFor="max_uses">Max Uses <span className="text-muted-foreground font-normal">(optional)</span></Label>
              <Input
                id="max_uses"
                type="number"
                min={1}
                placeholder="Unlimited"
                value={form.max_uses}
                onChange={(e) => setForm((f) => ({ ...f, max_uses: e.target.value }))}
                className="mt-1"
              />
            </div>
            <div>
              <Label htmlFor="expires_days">Expires In <span className="text-muted-foreground font-normal">(days, optional)</span></Label>
              <Input
                id="expires_days"
                type="number"
                min={1}
                placeholder="Never"
                value={form.expires_days}
                onChange={(e) => setForm((f) => ({ ...f, expires_days: e.target.value }))}
                className="mt-1"
              />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => draft.dismiss(() => { resetForm(); onOpenChange(false); })}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={!isValid || createMutation.isPending || draft.session?.presentation.submitting || draft.session?.presentation.cleanup}
          >
            {createMutation.isPending ? "Creating..." : "Create rule"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
