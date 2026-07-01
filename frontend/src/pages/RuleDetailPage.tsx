// ---------------------------------------------------------------------------
// RuleDetailPage — the rule's detail page. (bu-2ix8d.7)
//
// Adopts <Page archetype="detail"> shell per the detail-page-archetype spec
// (bu-1jh6i). The shell owns breadcrumbs, the h1 title (directive text),
// status pill (maturity), and all loading / empty states. The page body owns
// Tiers 3–5:
//   - DetailEyebrow (kind + short id)
//   - State line (maturity + permanence + scope)
//   - Outcome record (two mono lines)
//   - KV band, metadata block
//   - Provenance section (omitted when no source episode)
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
  KVBand,
  MetadataBlock,
  ProvenanceLink,
  ProvenanceSection,
  StateLine,
} from "@/components/memory/DetailSkeleton";
import { Mono } from "@/components/ui/Mono";
import { Badge } from "@/components/ui/badge";
import { Page } from "@/components/ui/page";
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

  // Title: the directive text is the record identity; truncate to 80 chars per spec.
  const title = rule
    ? rule.content.length > 80
      ? rule.content.slice(0, 80)
      : rule.content
    : "Rule";

  const harmful = rule?.harmful_count ?? 0;

  const provenance =
    rule?.source_episode_id != null ? (
      <ProvenanceLink
        to={`/memory/episodes/${rule.source_episode_id}`}
        label={`derived from episode ${shortFragment(rule.source_episode_id)}`}
      />
    ) : null;

  return (
    <Page
      archetype="detail"
      title={title}
      breadcrumbs={[{ label: "standing orders", href: "/memory?register=rules" }]}
      status={
        rule ? (
          <Badge variant="secondary">{rule.maturity}</Badge>
        ) : undefined
      }
      loading={isLoading}
      empty={
        !rule && !isLoading
          ? {
              title: "Rule not found",
              description: "This rule is not on the books.",
            }
          : null
      }
    >
      {rule && (
        <div className="mx-auto flex max-w-[680px] flex-col gap-6">
          <DetailEyebrow kind="rule" id={rule.id} />

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
        </div>
      )}
    </Page>
  );
}
