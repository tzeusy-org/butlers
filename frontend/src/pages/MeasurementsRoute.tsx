import { useExpectedSignals } from "@/hooks/use-health";
import MeasurementsPage from "./MeasurementsPage";

/** Own the route's acquisition read separately from its measurement view. */
export default function MeasurementsRoute() {
  const expectedSignals = useExpectedSignals();
  return <MeasurementsPage expectedSignals={expectedSignals} />;
}
