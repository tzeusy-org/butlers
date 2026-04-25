// ---------------------------------------------------------------------------
// ChroniclesPage — shell skeleton (bu-ig72b)
//
// Placeholder page for the Chronicler dashboard surface. Each labelled widget
// region will be filled by follow-up issues:
//   - Gantt area (bu-ig72b.5)
//   - Map area (bu-ig72b.6)
//   - Aggregations area (bu-ig72b.7)
// ---------------------------------------------------------------------------

export default function ChroniclesPage() {
  return (
    <div className="space-y-6">
      {/* Page heading */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Chronicles</h1>
        <p className="text-muted-foreground mt-1">
          Retrospective view of lived past time reconstructed from butler evidence.
        </p>
      </div>

      {/* Gantt area */}
      <section aria-label="Gantt area" className="rounded-lg border bg-card p-6 min-h-48">
        <h2 className="text-sm font-medium text-muted-foreground mb-2">Gantt area</h2>
        <p className="text-sm text-muted-foreground italic">Timeline / Gantt widget — coming soon.</p>
      </section>

      {/* Map area */}
      <section aria-label="Map area" className="rounded-lg border bg-card p-6 min-h-48">
        <h2 className="text-sm font-medium text-muted-foreground mb-2">Map area</h2>
        <p className="text-sm text-muted-foreground italic">Location map widget — coming soon.</p>
      </section>

      {/* Aggregations area */}
      <section aria-label="Aggregations area" className="rounded-lg border bg-card p-6 min-h-48">
        <h2 className="text-sm font-medium text-muted-foreground mb-2">Aggregations area</h2>
        <p className="text-sm text-muted-foreground italic">Time aggregations widget — coming soon.</p>
      </section>
    </div>
  )
}
