import ConditionTracker from "@/components/health/ConditionTracker";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function ConditionsPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight">Conditions</h1>
        <p className="text-muted-foreground mt-1">
          Add, edit, and track your health conditions and their current status.
          Changes here and entries logged via your Health butler stay in sync.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>All Conditions</CardTitle>
          <CardDescription>
            Add a condition, or edit one to update its status as it evolves.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ConditionTracker />
        </CardContent>
      </Card>
    </div>
  );
}
