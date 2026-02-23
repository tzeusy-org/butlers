/**
 * Filters tab placeholder content.
 *
 * Full implementation is in butlers-dsa4.4.3. This stub ensures the tab
 * renders without error while the full feature is in development.
 */

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export function FiltersTabContent() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Filters</CardTitle>
        <CardDescription>
          Deterministic ingestion policy — triage rules, thread affinity, and label filters.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">
          Filter controls will be implemented in a follow-up task.
        </p>
      </CardContent>
    </Card>
  );
}
