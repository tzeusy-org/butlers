/**
 * Post-approval teaching digest.
 *
 * Approval and standing-rule creation are intentionally separate actions:
 * the first makes the reviewed request proceed; the second requires an
 * explicit confirmation after showing the backend-redacted proposed scope.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import { getApprovalRuleSuggestions } from "@/api/index.ts";
import { useCreateRuleFromAction } from "@/hooks/use-approvals.ts";
import { Button } from "@/components/ui/button.tsx";

interface ApprovalTeachingDigestProps {
  actionId: string;
  onDismiss: () => void;
}

const TEACHING_PROMPT_CLASS =
  // eslint-disable-next-line no-restricted-syntax -- informational post-approval teaching prompt, not operational status.
  "mx-6 mt-4 rounded border border-blue-500/35 bg-blue-500/5 px-4 py-3";

export function ApprovalTeachingDigest({ actionId, onDismiss }: ApprovalTeachingDigestProps) {
  const [confirming, setConfirming] = useState(false);
  const suggestionQuery = useQuery({
    queryKey: ["approvals", "rules", "suggestions", actionId],
    queryFn: () => getApprovalRuleSuggestions(actionId),
    retry: false,
  });
  const createRule = useCreateRuleFromAction();

  const suggestion = suggestionQuery.data?.data;
  const error = suggestionQuery.error ?? createRule.error;

  return (
    <section
      aria-label="Teach this approval"
      className={TEACHING_PROMPT_CLASS}
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="text-sm text-foreground">
            Approved. <strong>Always allow this shape?</strong>
          </p>
          {suggestionQuery.isLoading && (
            <p className="mt-1 text-xs text-muted-foreground" role="status">
              Checking the proposed scope…
            </p>
          )}
          {suggestion && (
            <>
              <p className="mt-1 text-xs text-muted-foreground">
                Create a standing rule for <span className="font-mono">{suggestion.tool_name}</span>{" "}
                only after reviewing this redacted scope.
              </p>
              <details className="mt-2">
                <summary className="cursor-pointer text-xs font-medium text-foreground">
                  Proposed scope
                </summary>
                <pre className="mt-1 max-h-32 overflow-auto rounded bg-muted px-2 py-1.5 text-[11px] text-muted-foreground">
                  {JSON.stringify(suggestion.suggested_constraints, null, 2)}
                </pre>
              </details>
            </>
          )}
          {error && (
            <p className="mt-1 text-xs text-destructive" role="alert">
              Could not prepare a standing rule. No rule was created.
            </p>
          )}
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {suggestion && !confirming && (
            <Button size="sm" onClick={() => setConfirming(true)}>
              Always allow this shape
            </Button>
          )}
          {suggestion && confirming && (
            <Button
              size="sm"
              onClick={() => {
                createRule.mutate(
                  { action_id: actionId },
                  {
                    onSuccess: () => {
                      toast.success("Standing rule created");
                      onDismiss();
                    },
                  },
                );
              }}
              disabled={createRule.isPending}
            >
              {createRule.isPending ? "Creating…" : "Create standing rule"}
            </Button>
          )}
          {confirming && suggestion && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setConfirming(false)}
              disabled={createRule.isPending}
            >
              Back
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={onDismiss}
            disabled={createRule.isPending}
          >
            Keep asking
          </Button>
        </div>
      </div>
    </section>
  );
}
