import { useState } from "react";

import type { Medication } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useMedicationDoses, useMedications } from "@/hooks/use-health";
import { Time } from "@/components/ui/time";

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function MedicationSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 6 }, (_, i) => (
        <Card key={i}>
          <CardHeader>
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-4 w-48" />
          </CardHeader>
          <CardContent>
            <Skeleton className="h-4 w-24" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <EmptyStateUI
      title="No medications found."
      description="Medications appear here once the Health butler adds them to your record."
    />
  );
}

// ---------------------------------------------------------------------------
// DoseLog — expandable dose history for a single medication
// ---------------------------------------------------------------------------

function DoseLog({ medicationId, frequency }: { medicationId: string; frequency: string }) {
  const { data: doses, isLoading } = useMedicationDoses(medicationId);

  if (isLoading) {
    return (
      <div className="space-y-2 pt-2">
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }

  if (!doses || doses.length === 0) {
    return (
      <p className="text-muted-foreground pt-2 text-sm">No dose records yet.</p>
    );
  }

  // Simple adherence calculation: taken doses / total doses
  const totalDoses = doses.length;
  const takenDoses = doses.filter((d) => !d.skipped).length;
  const adherencePct = totalDoses > 0 ? Math.round((takenDoses / totalDoses) * 100) : 0;

  return (
    <div className="space-y-3 pt-3">
      {/* Adherence indicator */}
      <div className="flex items-center gap-3">
        <span className="text-muted-foreground text-sm">Adherence:</span>
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full transition-all"
            style={{
              width: `${adherencePct}%`,
              backgroundColor:
                adherencePct >= 80 ? "var(--severity-low)" : adherencePct >= 50 ? "var(--severity-medium)" : "var(--severity-high)",
            }}
          />
        </div>
        <span className="text-sm font-medium tabular-nums">{adherencePct}%</span>
      </div>
      <p className="text-muted-foreground text-xs">
        {takenDoses} of {totalDoses} doses taken ({frequency})
      </p>

      {/* Recent doses table */}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Date</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Notes</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {doses.slice(0, 20).map((dose) => (
            <TableRow key={dose.id}>
              <TableCell className="text-sm">
                <Time value={dose.taken_at} mode="absolute" precision="minute" compact />
              </TableCell>
              <TableCell>
                <Badge variant={dose.skipped ? "destructive" : "default"} className="text-xs">
                  {dose.skipped ? "Skipped" : "Taken"}
                </Badge>
              </TableCell>
              <TableCell className="text-muted-foreground max-w-xs truncate text-sm">
                {dose.notes ?? "\u2014"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MedicationCard
// ---------------------------------------------------------------------------

function MedicationCard({ medication }: { medication: Medication }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <Card className="cursor-pointer" onClick={() => setExpanded((v) => !v)}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">{medication.name}</CardTitle>
          <Badge variant={medication.active ? "default" : "secondary"} className="text-xs">
            {medication.active ? "Active" : "Inactive"}
          </Badge>
        </div>
        <CardDescription>{medication.dosage}</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-muted-foreground text-sm">
          Frequency: {medication.frequency}
        </p>
        {medication.notes && (
          <p className="text-muted-foreground mt-1 text-xs">{medication.notes}</p>
        )}

        {/* Expandable dose log */}
        {expanded && (
          <div onClick={(e) => e.stopPropagation()}>
            <DoseLog medicationId={medication.id} frequency={medication.frequency} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// MedicationTracker
// ---------------------------------------------------------------------------

export default function MedicationTracker() {
  const [showAll, setShowAll] = useState(false);

  const { data, isLoading } = useMedications({
    active: showAll ? undefined : true,
    limit: 100,
  });

  const medications = data?.data ?? [];

  return (
    <div className="space-y-4">
      {/* Filter toggle */}
      <div className="flex items-center gap-3">
        <Button
          variant={!showAll ? "default" : "outline"}
          size="sm"
          onClick={() => setShowAll(false)}
        >
          Active
        </Button>
        <Button
          variant={showAll ? "default" : "outline"}
          size="sm"
          onClick={() => setShowAll(true)}
        >
          All
        </Button>
      </div>

      {/* Medication cards */}
      {isLoading ? (
        <MedicationSkeleton />
      ) : medications.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {medications.map((med) => (
            <MedicationCard key={med.id} medication={med} />
          ))}
        </div>
      )}
    </div>
  );
}
