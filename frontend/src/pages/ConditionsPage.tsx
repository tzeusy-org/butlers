import ConditionTracker from "@/components/health/ConditionTracker";
import { Page } from "@/components/ui/page";

export default function ConditionsPage() {
  return (
    <Page
      archetype="list"
      title="Conditions"
      description="What you're carrying, and where each one stands. In sync with your Health butler."
    >
      <ConditionTracker />
    </Page>
  );
}
