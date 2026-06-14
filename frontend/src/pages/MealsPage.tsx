import MealTracker from "@/components/health/MealTracker";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function MealsPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight">Meals</h1>
        <p className="text-muted-foreground mt-1">
          Log, edit, and review meals with their nutrition and eating patterns.
          Changes here and entries logged via your Health butler stay in sync.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>All Meals</CardTitle>
          <CardDescription>
            Log a meal, or edit one to refine its type, time, nutrition, or notes.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <MealTracker />
        </CardContent>
      </Card>
    </div>
  );
}
