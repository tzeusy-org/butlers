// ---------------------------------------------------------------------------
// MedicationsPage — /medications [bu-w7b18.3]
//
// Reframed to the Dispatch language: a mono eyebrow + Display-500 headline +
// serif Voice lead, then the medication rule-list (no Card chrome). Dose
// adherence is sourced from the adherence route and stated plainly; doses are
// logged directly from each row.
// ---------------------------------------------------------------------------

import MedicationTracker from "@/components/health/MedicationTracker";
import { Display } from "@/components/ui/Display";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Voice } from "@/components/ui/Voice";

export default function MedicationsPage() {
  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <Eyebrow as="div">health · medications</Eyebrow>
        <Display>Medications</Display>
        <Voice className="max-w-2xl text-muted-foreground">
          Every medication you are on, with dose adherence drawn from the doses you and your Health
          butler log. Add or edit a medication, log a dose, and open any row for its dose history.
        </Voice>
      </header>

      <MedicationTracker />
    </div>
  );
}
