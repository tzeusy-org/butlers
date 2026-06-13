/**
 * Component tests for the shared detail-page skeleton primitives (bu-2ix8d.7).
 *
 * Acceptance ((memory house-ledger redesign, graduated) prompts/06-detail-pages.md):
 *   - DetailEyebrow renders `KIND · <short id>` (8 chars, hyphens stripped).
 *   - DetailHeading is the single H1 and dims to --dim when fading.
 *   - StateLine joins non-empty fragments with ` · ` and drops empties.
 *   - KVBand omits rows whose value is null/undefined/"" and renders nothing
 *     when every entry is empty.
 *   - ProvenanceSection is OMITTED entirely when given null children (no shell).
 */

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import {
  DetailEyebrow,
  DetailHeading,
  KVBand,
  ProvenanceLink,
  ProvenanceSection,
  StateLine,
} from "@/components/memory/DetailSkeleton";

function html(node: React.ReactNode): string {
  return renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
}

describe("DetailEyebrow", () => {
  it("renders KIND · short-id (8 chars, hyphens stripped, uppercase)", () => {
    const out = html(
      <DetailEyebrow kind="fact" id="7a3f21c9-dead-beef-0000-000000000000" />,
    );
    expect(out).toContain("FACT · 7A3F21C9");
  });
});

describe("DetailHeading", () => {
  it("renders an <h1>", () => {
    const out = html(<DetailHeading>hello</DetailHeading>);
    expect(out).toMatch(/<h1[^>]*>hello<\/h1>/);
  });

  it("dims to --dim when fading", () => {
    const out = html(<DetailHeading dimmed>fading</DetailHeading>);
    expect(out).toContain("var(--dim)");
  });

  it("uses --fg foreground when not fading", () => {
    const out = html(<DetailHeading>active</DetailHeading>);
    expect(out).toContain("var(--fg)");
    expect(out).not.toContain("var(--dim)");
  });
});

describe("StateLine", () => {
  it("joins non-empty fragments with ' · '", () => {
    const out = html(
      <StateLine fragments={["active", "standard permanence", "lifestyle scope"]} />,
    );
    expect(out).toContain("active · standard permanence · lifestyle scope");
  });

  it("drops empty/nullish fragments", () => {
    const out = html(<StateLine fragments={["active", null, "", undefined]} />);
    expect(out).toContain("active");
    expect(out).not.toContain("·");
  });

  it("renders nothing when all fragments are empty", () => {
    const out = html(<StateLine fragments={[null, "", undefined]} />);
    expect(out).toBe("");
  });
});

describe("KVBand", () => {
  it("renders rows with values and omits rows whose value is empty", () => {
    const out = html(
      <KVBand
        entries={[
          { key: "created", value: "2026-06-01" },
          { key: "source butler", value: null },
          { key: "tags", value: "" },
          { key: "references", value: 3 },
        ]}
      />,
    );
    expect(out).toContain("created");
    expect(out).toContain("2026-06-01");
    expect(out).toContain("references");
    expect(out).not.toContain("source butler");
    expect(out).not.toContain("tags");
  });

  it("renders nothing when every entry is empty", () => {
    const out = html(
      <KVBand entries={[{ key: "a", value: null }, { key: "b", value: "" }]} />,
    );
    expect(out).toBe("");
  });
});

describe("ProvenanceSection", () => {
  it("renders the PROVENANCE eyebrow + links when given children", () => {
    const out = html(
      <ProvenanceSection>
        <ProvenanceLink to="/memory/episodes/ep-1" label="derived from episode ep-1" />
      </ProvenanceSection>,
    );
    expect(out).toContain("PROVENANCE");
    expect(out).toContain("derived from episode ep-1");
    expect(out).toContain("/memory/episodes/ep-1");
  });

  it("is OMITTED entirely when children is null (no empty shell)", () => {
    const out = html(<ProvenanceSection>{null}</ProvenanceSection>);
    expect(out).toBe("");
  });
});
