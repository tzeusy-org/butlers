import type { FactAttribution } from "@/api/types";
import { Link } from "react-router";

/** Reporter availability and owner confirmation are independent of confidence. */
export function FactReporterLine({ fact }: { fact: FactAttribution }) {
  const reporter = fact.reported_by;
  const label = reporter?.availability === "available" && reporter.name
    ? `Reported by ${reporter.name}`
    : reporter?.availability === "system" ? "Recorded by system"
    : reporter?.availability === "unavailable" ? "Reported by an unknown sender (reporter unavailable)"
    : reporter?.availability === "unresolved" ? "Reported by an unresolved sender"
    : "Reporter unknown";
  return (
    <span className="text-xs text-muted-foreground">
      {reporter?.availability === "available" && reporter.entity_id
        ? <Link className="hover:underline" to={`/entities/${encodeURIComponent(reporter.entity_id)}`}>{label}</Link>
        : label}
      {fact.confirmed_at && <span className="ml-2" aria-label="Owner confirmed">Confirmed by you</span>}
    </span>
  );
}
