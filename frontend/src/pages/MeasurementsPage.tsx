import { ButlerManagedNote } from "@/components/health/ButlerManagedNote";
import MeasurementChart from "@/components/health/MeasurementChart";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function MeasurementsPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight">Measurements</h1>
        <p className="text-muted-foreground mt-1">
          Visualize your health measurements over time.
        </p>
        <ButlerManagedNote noun="Measurements" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Measurement Trends</CardTitle>
          <CardDescription>
            Select a measurement type and date range to view trends.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <MeasurementChart />
        </CardContent>
      </Card>
    </div>
  );
}
