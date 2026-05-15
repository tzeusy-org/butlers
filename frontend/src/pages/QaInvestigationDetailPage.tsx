import { useParams } from "react-router";

import { CaseDossier } from "@/components/qa";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { useQaCase } from "@/hooks/use-qa";

// ---------------------------------------------------------------------------
// QaInvestigationDetailPage
//
// Route: /qa/investigations/:attemptId
//
// Mounts the same CaseDossier component as `/qa?case=<id>`. The two routes
// share zero UX divergence; only the chrome (eyebrow, back-link) differs.
// ---------------------------------------------------------------------------

export default function QaInvestigationDetailPage() {
  const { attemptId = "" } = useParams<{ attemptId: string }>();

  // Fetch the case dossier early so we can derive short_id for the eyebrow
  // without duplicating the network call — CaseDossier uses the same query key.
  const caseQuery = useQaCase(attemptId || undefined);
  const caseData = caseQuery.data?.data?.case;

  const eyebrow = caseData
    ? `QA Investigation · ${caseData.short_id}`
    : "QA Investigation";

  if (!attemptId || (!caseQuery.isLoading && !caseQuery.data?.data && !caseQuery.isError)) {
    return (
      <div className="space-y-4">
        <Breadcrumbs items={[{ label: "QA", href: "/qa" }]} />
        <p className="font-[family-name:var(--font-serif,serif)] text-sm italic text-muted-foreground">
          Investigation not found.
        </p>
      </div>
    );
  }

  if (!caseQuery.isLoading && caseQuery.isError) {
    return (
      <div className="space-y-4">
        <Breadcrumbs items={[{ label: "QA", href: "/qa" }]} />
        <p className="font-[family-name:var(--font-serif,serif)] text-sm italic text-muted-foreground">
          Investigation not found.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[
          { label: "QA", href: "/qa" },
          { label: eyebrow },
        ]}
      />

      <CaseDossier caseId={attemptId} />
    </div>
  );
}
