import MedicationTracker from "@/components/health/MedicationTracker";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function MedicationsPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight">Medications</h1>
        <p className="text-muted-foreground mt-1">
          Add, edit, and track your medications and dose adherence over time.
          Changes here and entries logged via your Health butler stay in sync.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Medication Tracker</CardTitle>
          <CardDescription>
            Click a medication to view its dose history and adherence.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <MedicationTracker />
        </CardContent>
      </Card>
    </div>
  );
}
