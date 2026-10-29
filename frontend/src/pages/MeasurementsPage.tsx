import MeasurementChart from "@/components/health/MeasurementChart";
import MeasurementTracker from "@/components/health/MeasurementTracker";
import { Display } from "@/components/ui/Display";
import { Eyebrow } from "@/components/ui/Eyebrow";
import type { MeasurementSignalRead } from "@/lib/measurement-acquisition";

export default function MeasurementsPage({ expectedSignals }: { expectedSignals?: MeasurementSignalRead }) {
  return (
    <div className="max-w-5xl space-y-8">
      {/* Header — Display-500 headline, no card chrome */}
      <header className="space-y-2">
        <Eyebrow as="div">Health · Measurements</Eyebrow>
        <Display>Measurements</Display>
        <p className="text-muted-foreground mt-1 max-w-2xl">
          Your readings as a trajectory: what changed over time. Changes here and
          readings logged via your Health butler stay in sync.
        </p>
      </header>

      {/* Trend + chart — the leading surface */}
      <section className="space-y-3" aria-label="Trends">
        <MeasurementChart expectedSignals={expectedSignals} />
      </section>

      {/* Reading log — direct add/edit/delete */}
      <section className="space-y-3" aria-label="Reading log">
        <Eyebrow as="div">Reading log</Eyebrow>
        <MeasurementTracker />
      </section>
    </div>
  );
}
