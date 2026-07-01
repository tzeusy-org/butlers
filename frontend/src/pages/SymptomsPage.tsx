import SymptomTracker from "@/components/health/SymptomTracker";
import { Page } from "@/components/ui/page";

export default function SymptomsPage() {
  return (
    <Page
      archetype="list"
      title="Symptoms"
      description="A log of what you've felt, and how hard. In sync with your Health butler."
    >
      <SymptomTracker />
    </Page>
  );
}
