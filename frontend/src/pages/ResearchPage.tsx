import ResearchTracker from "@/components/health/ResearchTracker";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function ResearchPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight">Research</h1>
        <p className="text-muted-foreground mt-1">
          Add, edit, and organize your health research notes, articles, and
          references. Changes here and entries saved via your Health butler stay
          in sync.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Research Notes</CardTitle>
          <CardDescription>
            Add a note, or edit one to keep its findings up to date.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ResearchTracker />
        </CardContent>
      </Card>
    </div>
  );
}
