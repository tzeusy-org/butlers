import { useSearchParams } from "react-router";

import SymptomTracker from "@/components/health/SymptomTracker";
import { Page } from "@/components/ui/page";

export default function SymptomsPage() {
  const [searchParams] = useSearchParams();
  return (
    <Page
      archetype="list"
      title="Symptoms"
      description="A log of what you've felt, and how hard. In sync with your Health butler."
    >
      <SymptomTracker nameFromQuery={searchParams.get("name") ?? ""} />
    </Page>
  );
}
