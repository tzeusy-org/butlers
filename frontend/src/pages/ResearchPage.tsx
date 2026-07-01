import ResearchTracker from "@/components/health/ResearchTracker";
import { Page } from "@/components/ui/page";

export default function ResearchPage() {
  return (
    <Page
      archetype="list"
      title="Research"
      description="Notes, articles, and references you've gathered. In sync with your Health butler."
    >
      <ResearchTracker />
    </Page>
  );
}
