import { useState } from "react";

import IssuesPanel from "@/components/issues/IssuesPanel";
import { Button } from "@/components/ui/button";
import { useDismissIssue, useIssues, useUndismissIssue } from "@/hooks/use-issues";

export default function IssuesPage() {
  const [showDismissed, setShowDismissed] = useState(false);
  const { data, isLoading, isError } = useIssues(showDismissed);
  const dismiss = useDismissIssue();
  const undismiss = useUndismissIssue();
  const issues = data?.data ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-row items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Issues</h1>
          <p className="text-muted-foreground mt-1">
            {showDismissed
              ? "Dismissed issues. Restore one to return it to the active feed."
              : "Grouped errors and warnings across all butlers, newest first."}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setShowDismissed((prev) => !prev)}
        >
          {showDismissed ? "Show active" : "Show dismissed"}
        </Button>
      </div>

      <IssuesPanel
        issues={issues}
        isLoading={isLoading}
        isError={isError}
        dismissedView={showDismissed}
        onDismiss={(issueKey) => dismiss.mutate(issueKey)}
        isDismissing={dismiss.isPending}
        onRestore={(issueKey) => undismiss.mutate(issueKey)}
        isRestoring={undismiss.isPending}
      />
    </div>
  );
}
