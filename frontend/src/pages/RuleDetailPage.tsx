// ---------------------------------------------------------------------------
// RuleDetailPage — the rule's editorial detail page. (bu-2ix8d.7)
//
// Shares the DetailSkeleton shape with the fact and episode pages. Rule-specific
// pieces:
//   - Full directive text in sans 16px (NOT serif — serif is reserved for the
//     system's voice; a rule is system data).
//   - The outcome record, two mono lines:
//       applied 41 · helpful 38 · harmful 1 · effectiveness 0.86
//       last applied 2026-06-10 · last evaluated 2026-06-11
//     The `harmful` fragment renders --red ONLY when > 0 (zero harm → zero red).
//   - Provenance: `↳ derived from episode <short-id>` when source_episode_id is
//     set; the section is omitted otherwise.
//
// No commit footer — mutations live only on the fact page.
//
// Binding docs:
// - (memory house-ledger redesign, graduated) prompts/06-detail-pages.md "Rule"
// - (memory house-ledger redesign, graduated) MEMORY_LANGUAGE.md §4, §6
// ---------------------------------------------------------------------------

import { useParams } from "react-router";

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
import { useRule } from "@/hooks/use-memory";
import { permanenceTag } from "@/lib/memory-derived";
import { cn } from "@/lib/utils";

/** `2026-06-10` local date, or null for an unparseable timestamp. */
function fmtDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** First 8 chars of an id for inline provenance labels. */
function shortFragment(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

export default function RuleDetailPage() {
  const { ruleId } = useParams<{ ruleId: string }>();
  const { data, isLoading } = useRule(ruleId ?? null);
  const rule = data?.data;

  if (!rule) {
    return (
      <DetailSkeleton backHref="/memory?register=rules" backLabel="standing orders">
        <Voice variant="italic" className="py-6 text-[var(--mfg)]">
          {isLoading ? "Reading the standing orders…" : "This rule is not on the books."}
        </Voice>
      </DetailSkeleton>
    );
  }

  const harmful = rule.harmful_count;

  const provenance =
    rule.source_episode_id != null ? (
      <ProvenanceLink
        to={`/memory/episodes/${rule.source_episode_id}`}
        label={`derived from episode ${shortFragment(rule.source_episode_id)}`}
      />
    ) : null;

  return (
    <DetailSkeleton backHref="/memory?register=rules" backLabel="standing orders">
      <DetailEyebrow kind="rule" id={rule.id} />

      {/* Heading: the directive text is the headline (sans 24px/500 — the
          DetailHeading default; a rule is system data, never serif voice). */}
      <DetailHeading>{rule.content}</DetailHeading>

      {/* State line — maturity + permanence + scope, in the API's words. */}
      <StateLine
        fragments={[
          rule.maturity,
          `${rule.permanence} permanence`,
          rule.scope ? `${rule.scope} scope` : null,
        ]}
      />

      {/* Outcome record — two mono lines. harmful goes --red only when > 0. */}
      <div className="flex flex-col gap-1">
        <Mono className="tabular-nums">
          applied {rule.applied_count} · helpful {rule.success_count} ·{" "}
          <span className={cn(harmful > 0 && "text-[var(--red)]")}>
            harmful {harmful}
          </span>{" "}
          · effectiveness {rule.effectiveness_score.toFixed(2)}
        </Mono>
        <Mono muted className="tabular-nums">
          {[
            rule.last_applied_at ? `last applied ${fmtDate(rule.last_applied_at)}` : null,
            rule.last_evaluated_at ? `last evaluated ${fmtDate(rule.last_evaluated_at)}` : null,
          ]
            .filter(Boolean)
            .join(" · ") || "never applied"}
        </Mono>
      </div>

      {/* KV band — empty keys omitted. */}
      <KVBand
        entries={[
          { key: "permanence", value: <span className="font-mono text-[11px] tabular-nums">{permanenceTag(rule.permanence)}</span> },
          { key: "confidence", value: <Mono>{rule.confidence.toFixed(2)}</Mono> },
          { key: "decay rate", value: <Mono>{rule.decay_rate.toFixed(3)}/day</Mono> },
          { key: "created", value: <Mono>{fmtDate(rule.created_at)}</Mono> },
          { key: "source butler", value: rule.source_butler ? <Mono>{rule.source_butler}</Mono> : null },
          { key: "tags", value: rule.tags.length > 0 ? rule.tags.join(", ") : null },
        ]}
      />

      {/* Metadata — raw bag as a mono code block; omitted when empty. */}
      <MetadataBlock metadata={rule.metadata} />

      {/* Provenance — omitted when no source episode. */}
      <ProvenanceSection>{provenance}</ProvenanceSection>
    </DetailSkeleton>
  );
}
