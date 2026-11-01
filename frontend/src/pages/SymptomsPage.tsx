import SymptomTracker from "@/components/health/SymptomTracker";
import { Page } from "@/components/ui/page";

export default function SymptomsPage({ nameFromQuery = "" }: { nameFromQuery?: string }) {
  return (
    <Page
      archetype="list"
      title="Symptoms"
      description="A log of what you've felt, and how hard. In sync with your Health butler."
    >
      <SymptomTracker nameFromQuery={nameFromQuery} />
    </Page>
  );
}
