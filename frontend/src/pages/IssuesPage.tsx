import { useMemo, useState } from "react";

import IssuesPanel from "@/components/issues/IssuesPanel";
import { Button } from "@/components/ui/button";
import { Page } from "@/components/ui/page";
import { useDismissIssue, useIssues, useUndismissIssue } from "@/hooks/use-issues";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";

export default function IssuesPage() {
  const [showDismissed, setShowDismissed] = useState(false);
  const { data, isLoading, isError } = useIssues(showDismissed);
  const dismiss = useDismissIssue();
  const undismiss = useUndismissIssue();
  const issues = useMemo(() => data?.data ?? [], [data]);

  // -------------------------------------------------------------------
  // Command menu Actions (bu-86c4c.7 — per-page command registration API).
  // "Acknowledge issue" dismisses the newest active issue — dismissal is
  // this page's existing acknowledgment mechanism (there is no separate
  // "acknowledged" state), so the command reuses it rather than inventing a
  // parallel one.
  // -------------------------------------------------------------------
  const commandMenuCommands = useMemo<PaletteCommand[]>(() => {
    if (showDismissed || issues.length === 0) return [];
    const nextIssueKey = issues[0].issue_key;
    return [
      {
        id: "acknowledge-issue",
        label: "Acknowledge issue",
        keywords: ["dismiss", "issue"],
        perform: () => dismiss.mutate(nextIssueKey),
      },
    ];
  }, [showDismissed, issues, dismiss]);
  useRegisterCommands(commandMenuCommands);

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
