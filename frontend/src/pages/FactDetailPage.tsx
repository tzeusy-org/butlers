// ---------------------------------------------------------------------------
// FactDetailPage — the fact's editorial detail page. (bu-2ix8d.7)
//
// Shares the DetailSkeleton shape with the rule and episode pages. Fact-specific
// pieces:
//   - The decay-arithmetic line (mono, honest): confidence · decay · last
//     confirmed · effective.
//   - Entity anchors out: subject → /entities/:entity_id, object →
//     /entities/:object_entity_id.
//   - Supersession links, BOTH directions when present in the payload:
//     `supersedes` (forward, the fact's own supersedes_id) AND `superseded by`
//     (reverse, the payload's superseded_by from bu-awo8k.8). Either link is
//     omitted when its id is absent.
//   - The commit footer (Confirm / Retract). Both endpoints are LIVE on main
//     (bu-awo8k.3 / .4), so the footer ALWAYS renders here — never a dead button.
//
// Binding docs:
// - (memory house-ledger redesign, graduated) prompts/06-detail-pages.md "Fact" + "Commit footer"
// - (memory house-ledger redesign, graduated) VISION.md (commit pills)
// ---------------------------------------------------------------------------

import { type ReactElement, useState } from "react";
import { Link, useParams } from "react-router";

import {
  DetailEyebrow,
  DetailHeading,
  DetailSkeleton,
  KVBand,
  MetadataBlock,
  ProvenanceLink,
  ProvenanceSection,
  StateLine,
} from "@/components/memory/DetailSkeleton";
import { Mono } from "@/components/ui/Mono";
import { Voice } from "@/components/ui/Voice";
import { useConfirmFact, useFact, useRetractFact } from "@/hooks/use-memory";
import { decayArithmeticLine, permanenceTag } from "@/lib/memory-derived";
import { cn } from "@/lib/utils";
import type { Fact } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Date helper
// ---------------------------------------------------------------------------

/** `2026-06-02` local date, or "—" for an unparseable timestamp. */
function fmtDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// ---------------------------------------------------------------------------
// Subject / object entity anchors
// ---------------------------------------------------------------------------

/** Underlined entity anchor out to /entities/:id, or plain text when unlinked. */
function EntityAnchor({
  id,
  name,
  fallback,
}: {
  id: string | null;
  name: string | null;
  fallback: string;
}) {
  const label = name ?? fallback;
  if (!id) return <span>{label}</span>;
  return (
    <Link
      to={`/entities/${id}`}
      className="underline [text-underline-offset:3px] hover:text-[var(--fg)]"
    >
      {label}
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Commit footer (Confirm / Retract)
// ---------------------------------------------------------------------------

/**
 * The only mutations on the entire memory surface. Both endpoints are live, so
 * this footer always renders. `Confirm` is the single commit-class pill
 * (fg-on-bg); `Retract` is secondary (bordered). Retract requires a one-step
 * confirm: the pill becomes `Retract (confirm?)` for 5s, no modal.
 */
function CommitFooter({ fact }: { fact: Fact }) {
  const confirmMutation = useConfirmFact();
  const retractMutation = useRetractFact();
  const [retractArmed, setRetractArmed] = useState(false);

  const busy = confirmMutation.isPending || retractMutation.isPending;

  const onRetract = () => {
    if (!retractArmed) {
      setRetractArmed(true);
      // Disarm after 5s if the owner does not follow through.
      window.setTimeout(() => setRetractArmed(false), 5000);
      return;
    }
    setRetractArmed(false);
    retractMutation.mutate(fact.id);
  };

  return (
    <footer className="flex flex-col gap-3 border-t border-[var(--border-soft)] pt-5">
      <div className="flex flex-wrap items-center gap-3">
        {/* Confirm — the commit pill (fg-on-bg). */}
        <button
          type="button"
          disabled={busy}
          onClick={() => confirmMutation.mutate(fact.id)}
          className={cn(
            "inline-flex h-7 items-center rounded-full px-3.5",
            "font-mono text-[11px] font-medium",
            "bg-[var(--fg)] text-[var(--bg)]",
            "transition-opacity hover:opacity-90",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30",
            "disabled:pointer-events-none disabled:opacity-40",
          )}
        >
          Confirm
        </button>
        <Voice variant="italic" as="span" className="text-[13px] text-[var(--mfg)]">
          Re-inks the fact: resets decay from today.
        </Voice>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        {/* Retract — secondary (bordered, not colored). One-step confirm. */}
        <button
          type="button"
          disabled={busy}
          onClick={onRetract}
          className={cn(
            "inline-flex h-7 items-center rounded-full px-3.5",
            "font-mono text-[11px] font-medium",
            "border border-[var(--border)] bg-transparent text-[var(--fg)]",
            "transition-colors hover:border-[var(--fg)]",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30",
            "disabled:pointer-events-none disabled:opacity-40",
          )}
        >
          {retractArmed ? "Retract (confirm?)" : "Retract"}
        </button>
        <Voice variant="italic" as="span" className="text-[13px] text-[var(--mfg)]">
          Marks the record incorrect; agents stop retrieving it.
        </Voice>
      </div>
    </footer>
  );
}

// ---------------------------------------------------------------------------
// FactDetailPage
// ---------------------------------------------------------------------------

interface FactDetailPageProps {
  /** Reference instant for the decay line. Injectable for deterministic tests. */
  now?: Date;
}

export default function FactDetailPage({ now }: FactDetailPageProps = {}) {
  const { factId } = useParams<{ factId: string }>();
  const { data, isLoading } = useFact(factId ?? null);
  const fact = data?.data;

  if (!fact) {
    return (
      <DetailSkeleton backHref="/memory" backLabel="ledger">
        <Voice variant="italic" className="py-6 text-[var(--mfg)]">
          {isLoading ? "Reading the ledger…" : "This fact is not in the ledger."}
        </Voice>
      </DetailSkeleton>
    );
  }

  // The server owns the fading threshold — dim the whole page on validity.
  const dimmed = fact.validity === "fading";

  // Supersession, both directions: forward from the fact's own supersedes_id,
  // reverse from the payload's superseded_by (bu-awo8k.8).
  const supersededById = fact.superseded_by ?? null;

  // Provenance children: only render the section when at least one chain link
  // exists. Empty provenance OMITS the section entirely (no empty shell).
  const provenanceLinks = [
    fact.source_episode_id != null ? (
      <ProvenanceLink
        key="episode"
        to={`/memory/episodes/${fact.source_episode_id}`}
        label={`derived from episode ${shortFragment(fact.source_episode_id)}`}
      />
    ) : null,
    fact.supersedes_id != null ? (
      <ProvenanceLink
        key="supersedes"
        to={`/memory/facts/${fact.supersedes_id}`}
        label={`supersedes ${shortFragment(fact.supersedes_id)}`}
      />
    ) : null,
    supersededById != null ? (
      <ProvenanceLink
        key="superseded-by"
        to={`/memory/facts/${supersededById}`}
        label={`superseded by ${shortFragment(supersededById)}`}
      />
    ) : null,
  ].filter((x): x is ReactElement => x != null);

  return (
    <DetailSkeleton backHref="/memory" backLabel="ledger">
      <DetailEyebrow kind="fact" id={fact.id} />

      {/* Heading: the content is the headline; subject · predicate is the
          record-identity subtitle below it (per the detail-page archetype). */}
      <DetailHeading dimmed={dimmed} subtitle={`${fact.subject} · ${fact.predicate}`}>
        {fact.content}
      </DetailHeading>

      {/* State line — lifecycle in the API's words. */}
      <StateLine
        dimmed={dimmed}
        fragments={[
          fact.validity,
          `${fact.permanence} permanence`,
          fact.scope ? `${fact.scope} scope` : null,
        ]}
      />

      {/* Decay arithmetic — one honest mono line. */}
      <Mono
        muted={dimmed}
        className={cn("tabular-nums", dimmed && "text-[var(--dim)]")}
      >
        {decayArithmeticLine(fact, now)}
      </Mono>

      {/* KV band — empty keys omitted. */}
      <KVBand
        entries={[
          { key: "subject", value: <EntityAnchor id={fact.entity_id} name={fact.entity_name} fallback={fact.subject} /> },
          { key: "predicate", value: <span className="font-mono text-[11px]">{fact.predicate}</span> },
          {
            key: "object",
            value:
              fact.object_entity_id != null || fact.object_entity_name != null ? (
                <EntityAnchor id={fact.object_entity_id} name={fact.object_entity_name} fallback={fact.content} />
              ) : null,
          },
          { key: "permanence", value: <span className="font-mono text-[11px] tabular-nums">{permanenceTag(fact.permanence)}</span> },
          { key: "created", value: <Mono>{fmtDate(fact.created_at)}</Mono> },
          { key: "last referenced", value: fact.last_referenced_at ? <Mono>{fmtDate(fact.last_referenced_at)}</Mono> : null },
          { key: "last confirmed", value: fact.last_confirmed_at ? <Mono>{fmtDate(fact.last_confirmed_at)}</Mono> : null },
          { key: "references", value: <Mono>{fact.reference_count}</Mono> },
          { key: "source butler", value: fact.source_butler ? <Mono>{fact.source_butler}</Mono> : null },
          { key: "tags", value: fact.tags.length > 0 ? fact.tags.join(", ") : null },
        ]}
      />

      {/* Metadata — raw bag as a mono code block; omitted when empty. */}
      <MetadataBlock metadata={fact.metadata} />

      {/* Provenance & cross-references — omitted when empty. */}
      <ProvenanceSection>
        {provenanceLinks.length > 0 ? <>{provenanceLinks}</> : null}
      </ProvenanceSection>

      {/* Commit footer — both endpoints live, always rendered. */}
      <CommitFooter fact={fact} />
    </DetailSkeleton>
  );
}

/** First 8 chars of an id for inline provenance labels (no hyphen strip). */
function shortFragment(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}
