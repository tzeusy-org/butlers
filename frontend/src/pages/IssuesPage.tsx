import { useState } from "react";

import IssuesPanel from "@/components/issues/IssuesPanel";
import { Button } from "@/components/ui/button";
import { Page } from "@/components/ui/page";
import { useDismissIssue, useIssues, useUndismissIssue } from "@/hooks/use-issues";

export default function IssuesPage() {
  const [showDismissed, setShowDismissed] = useState(false);
  const { data, isLoading, isError } = useIssues(showDismissed);
  const dismiss = useDismissIssue();
  const undismiss = useUndismissIssue();
  const issues = data?.data ?? [];

  return (
    <Page
      archetype="list"
      title="Issues"
      description={
        showDismissed
          ? "Dismissed issues. Restore one to return it to the active feed."
          : "Grouped errors and warnings across all butlers, newest first."
      }
      actions={
        <Button
          variant="outline"
          size="sm"
          onClick={() => setShowDismissed((prev) => !prev)}
        >
          {showDismissed ? "Show active" : "Show dismissed"}
        </Button>
      }
    >
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
    </Page>
  );
}
