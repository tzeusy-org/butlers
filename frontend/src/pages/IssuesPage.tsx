import IssuesPanel from "@/components/issues/IssuesPanel";
import { useIssues } from "@/hooks/use-issues";

export default function IssuesPage() {
  const { data, isLoading } = useIssues();
  const issues = data?.data ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Issues</h1>
        <p className="text-muted-foreground mt-1">
          Grouped errors and warnings across all butlers, newest first.
        </p>
      </div>

      <IssuesPanel issues={issues} isLoading={isLoading} />
    </div>
  );
}
