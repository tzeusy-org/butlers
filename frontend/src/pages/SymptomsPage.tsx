import SymptomTracker from "@/components/health/SymptomTracker";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function SymptomsPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight">Symptoms</h1>
        <p className="text-muted-foreground mt-1">
          Log, edit, and review symptom occurrences with their severity ratings.
          Changes here and entries logged via your Health butler stay in sync.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>All Symptoms</CardTitle>
          <CardDescription>
            Log a symptom, or edit one to refine its severity, date, or notes.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <SymptomTracker />
        </CardContent>
      </Card>
    </div>
  );
}
