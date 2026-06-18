/**
 * PracticalDrawer
 *
 * A collapsible section near the top of the entity detail page that holds
 * practical / administrative details: linked contact, credentials link,
 * provenance, and any other owner-specific setup.
 *
 * Open by default; `forceOpen` drives the "action needed" affordance when the
 * owner entity still needs setup (no linked contact).
 */

import { useId, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { Time } from "@/components/ui/time";

// ---------------------------------------------------------------------------
// ProvenanceFooter
// ---------------------------------------------------------------------------

const DISPLAY_EXCLUDED = new Set(["source_butler", "source_scope", "unidentified"]);

function ProvenanceFooter({
  entity,
}: {
  entity: { metadata: Record<string, unknown>; created_at: string; updated_at: string };
}) {
  const sourceButler = entity.metadata?.source_butler;
  const sourceScope = entity.metadata?.source_scope;
  const extraMetadata = Object.fromEntries(
    Object.entries(entity.metadata).filter(([k]) => !DISPLAY_EXCLUDED.has(k)),
  );
  const hasExtra = Object.keys(extraMetadata).length > 0;

  return (
    <div className="text-muted-foreground space-y-2 border-t pt-3 text-xs">
      <div className="flex flex-wrap gap-x-6 gap-y-1">
        {!!sourceButler && (
          <span>
            Source butler:{" "}
            <span className="text-foreground font-medium">{String(sourceButler)}</span>
          </span>
        )}
        {!!sourceScope && (
          <span>
            Scope:{" "}
            <span className="text-foreground font-medium">{String(sourceScope)}</span>
          </span>
        )}
        <span>Created <Time value={entity.created_at} mode="absolute" precision="day" /></span>
        <span>Updated <Time value={entity.updated_at} mode="absolute" precision="day" /></span>
      </div>
      {hasExtra && (
        <details>
          <summary className="cursor-pointer text-xs hover:text-foreground">
            Raw metadata
          </summary>
          <pre className="bg-muted mt-2 overflow-x-auto rounded p-3 text-[11px]">
            {JSON.stringify(extraMetadata, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PracticalDrawer
// ---------------------------------------------------------------------------

export interface PracticalDrawerProps {
  entity: { metadata: Record<string, unknown>; created_at: string; updated_at: string };
  forceOpen: boolean;
  children: React.ReactNode;
}

export function PracticalDrawer({ entity, forceOpen, children }: PracticalDrawerProps) {
  // Open by default — practical details are first-class content on the entity
  // page, not a hidden footer. `forceOpen` still drives the "action needed"
  // affordance when owner setup is incomplete. Users can collapse manually.
  const [open, setOpen] = useState(true);
  const panelId = useId();

  return (
    <section className="rounded-md border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={panelId}
        className="hover:bg-muted/40 flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors"
      >
        <span className="text-sm font-medium">
          Contact Details
          {forceOpen && (
            <span className="text-muted-foreground ml-2 text-xs">
              (action needed)
            </span>
          )}
        </span>
        {open ? (
          <ChevronDown className="text-muted-foreground h-4 w-4" />
        ) : (
          <ChevronRight className="text-muted-foreground h-4 w-4" />
        )}
      </button>
      <div
        id={panelId}
        role="region"
        aria-label="Contact Details"
        hidden={!open}
        className="space-y-4 border-t px-4 py-4"
      >
        {children}
        <ProvenanceFooter entity={entity} />
      </div>
    </section>
  );
}
