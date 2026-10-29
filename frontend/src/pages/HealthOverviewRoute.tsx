import { useExpectedSignals } from "@/hooks/use-health";
import HealthOverviewPage from "./HealthOverviewPage";

/** Route query ownership keeps the overview view usable with explicit read state. */
export default function HealthOverviewRoute() {
  const expectedSignals = useExpectedSignals();
  return <HealthOverviewPage expectedSignals={expectedSignals} />;
}
