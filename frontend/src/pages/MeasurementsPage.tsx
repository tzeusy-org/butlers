import MeasurementChart from "@/components/health/MeasurementChart";
import MeasurementTracker from "@/components/health/MeasurementTracker";
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
          Log, edit, and review your health measurements. Changes here and
          readings logged via your Health butler stay in sync.
        </p>
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

      <Card>
        <CardHeader>
          <CardTitle>All Measurements</CardTitle>
          <CardDescription>
            Log a reading, or edit one to refine its value, date, or notes.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <MeasurementTracker />
        </CardContent>
      </Card>
    </div>
  );
}
