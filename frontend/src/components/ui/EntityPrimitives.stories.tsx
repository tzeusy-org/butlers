// ---------------------------------------------------------------------------
// EntityPrimitives.stories — shared entity + provenance primitives (bu-ovq7t)
//
// Visual gallery for the single-source entity UI primitives consumed by every
// entity view (Index, Hop, Columns, Concentration, Detail, Finder):
//   EntityMark · TierBadge · StateDot · Row · ConfBar · StalenessBand ·
//   ProvenanceMarks.
//
// The provenance section makes the binding invariant visible: confidence and
// staleness are two distinct axes that never blend — including the headline
// case of a fully-confident yet stale fact.
// ---------------------------------------------------------------------------

import { EntityMark } from "./EntityMark"
import { ConfBar, ProvenanceMarks, StalenessBand } from "./Provenance"
import { Row } from "./Row"
import { StateDot, type EntityState } from "./StateDot"
import { TierBadge, type DunbarTier } from "./TierBadge"

const ENTITY_TYPES = [
  "person",
  "organization",
  "location",
  "product",
  "account",
  "event",
  "group",
  "other",
] as const

const TIERS: DunbarTier[] = [5, 15, 50, 150, 500, 1500]
const STATES: EntityState[] = ["healthy", "unidentified", "duplicate-candidate", "stale", "archived"]

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: 32 }}>
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          textTransform: "uppercase",
          letterSpacing: "0.14em",
          color: "var(--mfg)",
          marginBottom: 12,
        }}
      >
        {title}
      </div>
      {children}
    </section>
  )
}

export default {
  title: "ui/EntityPrimitives",
}

export const Marks = () => (
  <div style={{ padding: 24 }}>
    <Section title="EntityMark · neutral">
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        {ENTITY_TYPES.map((t) => (
          <EntityMark key={t} name="Alice Johnson" entityType={t} />
        ))}
      </div>
    </Section>
    <Section title="EntityMark · fill (active)">
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        {ENTITY_TYPES.map((t) => (
          <EntityMark key={t} name="Alice Johnson" entityType={t} tone="fill" />
        ))}
      </div>
    </Section>
    <Section title="EntityMark · ownership / state borders">
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <EntityMark name="Owner" entityType="person" isOwner />
        <EntityMark name="Unknown" entityType="person" isUnidentified />
      </div>
    </Section>
  </div>
)

export const TierBadges = () => (
  <div style={{ padding: 24 }}>
    <Section title="TierBadge · Dunbar ramp">
      <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
        {TIERS.map((tier) => (
          <TierBadge key={tier} tier={tier} />
        ))}
      </div>
    </Section>
  </div>
)

export const StateDots = () => (
  <div style={{ padding: 24 }}>
    <Section title="StateDot · curation states">
      <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
        {STATES.map((state) => (
          <span key={state} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <StateDot state={state} />
            <span style={{ fontSize: 12, color: "var(--mfg)" }}>{state}</span>
          </span>
        ))}
      </div>
    </Section>
  </div>
)

export const Rows = () => (
  <div style={{ padding: 24, maxWidth: 520 }}>
    <Section title="Row · the canonical list primitive">
      {[
        { name: "Alice Johnson", type: "person", owner: true },
        { name: "Acme Corp", type: "organization", owner: false },
        { name: "London", type: "location", owner: false },
      ].map((e) => (
        <Row
          key={e.name}
          interactive
          mark={<EntityMark name={e.name} entityType={e.type} isOwner={e.owner} />}
          meta={<TierBadge tier={150} />}
        >
          <span style={{ fontWeight: 500 }}>{e.name}</span>
        </Row>
      ))}
    </Section>
  </div>
)

export const Provenance = () => (
  <div style={{ padding: 24, maxWidth: 520 }}>
    <Section title="ConfBar · confidence axis">
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {[1.0, 0.9, 0.85, 0.7, 0.4].map((c) => (
          <span key={c} style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
            <ConfBar conf={c} />
            <span
              style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--mfg)" }}
            >
              {c.toFixed(2)}
            </span>
          </span>
        ))}
      </div>
    </Section>

    <Section title="StalenessBand · staleness axis">
      <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
        <StalenessBand band="fresh" />
        <StalenessBand band="aging" />
        <StalenessBand band="stale" />
      </div>
    </Section>

    <Section title="ProvenanceMarks · src + verified">
      <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
        <ProvenanceMarks src="relationship" verified />
        <ProvenanceMarks src="memory" verified={false} />
      </div>
    </Section>

    <Section title="Two axes, never blended (conf=1.0 AND stale)">
      <Row
        meta={
          <span style={{ display: "inline-flex", alignItems: "center", gap: 12 }}>
            <ConfBar conf={1.0} />
            <StalenessBand band="stale" />
            <ProvenanceMarks src="relationship" verified />
          </span>
        }
      >
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
          has-email · alice@example.com
        </span>
      </Row>
      <p
        style={{
          fontFamily: "var(--font-serif)",
          fontStyle: "italic",
          fontSize: 14,
          color: "var(--mfg)",
          marginTop: 12,
          maxWidth: "50ch",
        }}
      >
        The bar is full and the band is stale at the same time. Confidence and
        staleness are separate axes; the system never collapses them into one
        score.
      </p>
    </Section>
  </div>
)
