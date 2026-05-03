// @vitest-environment jsdom
/**
 * Tests for <DetailPage> shell.
 *
 * Static markup tests use renderToStaticMarkup (no DOM lifecycle needed).
 * The four-tier density contract is verified through slot rendering assertions.
 */

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { DetailPage, type DetailPageProps } from "@/components/layout/DetailPage";

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

type PartialDetailPageProps = Partial<Omit<DetailPageProps, "record">> &
  Pick<DetailPageProps, "record">;

function render(props: PartialDetailPageProps): string {
  const full: DetailPageProps = {
    ...props,
    primary: props.primary ?? <div>Primary content</div>,
  };
  return renderToStaticMarkup(
    <MemoryRouter>
      <DetailPage {...full} />
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Hero — record identity
// ---------------------------------------------------------------------------

describe("DetailPage -- hero", () => {
  it("renders record.title as the single H1 (no duplicate headings)", () => {
    const html = render({ record: { title: "Alice Smith" }, primary: <p>main</p> });
    // Count H1 occurrences — must be exactly one
    const h1Matches = html.match(/<h1[^>]*>/g) ?? [];
    expect(h1Matches).toHaveLength(1);
    expect(html).toContain("Alice Smith");
  });

  it("renders record.subtitle below the title in muted color", () => {
    const html = render({
      record: { title: "Alice Smith", subtitle: "alice@example.com" },
      primary: <p>main</p>,
    });
    expect(html).toContain("alice@example.com");
    expect(html).toContain("text-muted-foreground");
  });

  it("renders record.type as a pill badge when provided", () => {
    const html = render({
      record: { title: "Alice Smith", type: "person" },
      primary: <p>main</p>,
    });
    expect(html).toContain("person");
    expect(html).toContain("rounded-full");
  });

  it("does not render a type pill when record.type is omitted", () => {
    const html = render({ record: { title: "Alice Smith" }, primary: <p>main</p> });
    expect(html).not.toContain("rounded-full");
  });

  it("renders actions to the right of the title row", () => {
    const html = render({
      record: { title: "Alice Smith" },
      primary: <p>main</p>,
      actions: <button>Edit</button>,
    });
    expect(html).toContain("Edit");
  });
});

// ---------------------------------------------------------------------------
// Primary slot
// ---------------------------------------------------------------------------

describe("DetailPage -- primary slot", () => {
  it("renders primary content in the page body", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <section data-testid="primary-content">Primary section</section>,
    });
    expect(html).toContain("Primary section");
  });

  it("renders primary after the hero block", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <div id="primary-marker">primary</div>,
    });
    const titlePos = html.indexOf("Test Record");
    const primaryPos = html.indexOf("primary-marker");
    expect(titlePos).toBeLessThan(primaryPos);
  });
});

// ---------------------------------------------------------------------------
// Supporting grid — 2-column on lg+
// ---------------------------------------------------------------------------

describe("DetailPage -- supporting grid", () => {
  it("renders supporting content wrapped in a 2-column grid", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <p>main</p>,
      supporting: (
        <>
          <div>Panel A</div>
          <div>Panel B</div>
        </>
      ),
    });
    // Should use a grid layout with 2 columns on lg
    expect(html).toContain("lg:grid-cols-2");
    expect(html).toContain("Panel A");
    expect(html).toContain("Panel B");
  });

  it("does not render supporting wrapper when supporting is null", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <p>main</p>,
      supporting: null,
    });
    expect(html).not.toContain("lg:grid-cols-2");
  });

  it("does not render supporting wrapper when supporting is omitted", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <p>main</p>,
    });
    expect(html).not.toContain("lg:grid-cols-2");
  });
});

// ---------------------------------------------------------------------------
// Practical drawer — consumer-supplied ReactNode
// ---------------------------------------------------------------------------

describe("DetailPage -- practical drawer", () => {
  it("renders practical content when provided as ReactNode", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <p>main</p>,
      practical: (
        <section>
          <button type="button">Practical details</button>
          <div>Settings content</div>
        </section>
      ),
    });
    expect(html).toContain("Practical details");
    expect(html).toContain("Settings content");
  });

  it("does not render practical slot when practical is null", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <p>main</p>,
      practical: null,
    });
    expect(html).not.toContain("Practical details");
  });

  it("does not render practical slot when practical is omitted", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <p>main</p>,
    });
    expect(html).not.toContain("Practical details");
  });
});

// ---------------------------------------------------------------------------
// Pulse strip — consumer-supplied ReactNode
// ---------------------------------------------------------------------------

describe("DetailPage -- pulse strip", () => {
  it("renders pulse content when provided as ReactNode", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <p>main</p>,
      pulse: (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div>
            <p>Last contact</p>
            <p>2 weeks ago</p>
          </div>
          <div>
            <p>Open loops</p>
            <p>None</p>
          </div>
        </div>
      ),
    });
    expect(html).toContain("Last contact");
    expect(html).toContain("2 weeks ago");
    expect(html).toContain("Open loops");
  });

  it("does not render pulse strip when pulse is null", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <p>main</p>,
      pulse: null,
    });
    // Grid from PulseStrip should not appear without explicit content
    expect(html).not.toContain("sm:grid-cols-4");
  });

  it("pulse strip appears between hero and primary content in render order", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <div id="primary-slot">primary</div>,
      pulse: <div id="pulse-marker">Cadence: Active</div>,
    });
    const pulsePos = html.indexOf("pulse-marker");
    const primaryPos = html.indexOf("primary-slot");
    // Hero (title) < pulse strip < primary content
    const titlePos = html.indexOf("Test Record");
    expect(titlePos).toBeLessThan(pulsePos);
    expect(pulsePos).toBeLessThan(primaryPos);
  });
});

// ---------------------------------------------------------------------------
// Auxiliary slot
// ---------------------------------------------------------------------------

describe("DetailPage -- auxiliary slot", () => {
  it("renders auxiliary content when provided", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <p>main</p>,
      auxiliary: <section>Auxiliary section</section>,
    });
    expect(html).toContain("Auxiliary section");
  });

  it("does not render auxiliary wrapper when auxiliary is null", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <p>main</p>,
      auxiliary: null,
    });
    // No auxiliary-specific content
    expect(html).not.toContain("Auxiliary section");
  });
});

// ---------------------------------------------------------------------------
// Breadcrumbs
// ---------------------------------------------------------------------------

describe("DetailPage -- breadcrumbs", () => {
  it("forwards breadcrumbs to <Page>", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <p>main</p>,
      breadcrumbs: [
        { label: "Home", href: "/" },
        { label: "Records", href: "/records" },
        { label: "Test Record" },
      ],
    });
    expect(html).toContain("Home");
    expect(html).toContain("Records");
  });
});

// ---------------------------------------------------------------------------
// Loading and error states
// ---------------------------------------------------------------------------

describe("DetailPage -- loading/error states", () => {
  it("renders loading skeleton when loading is true", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <p>main</p>,
      loading: true,
    });
    // Loading state shows skeleton, not primary content
    expect(html).toContain("Loading");
    expect(html).not.toContain("main");
  });

  it("renders error state when error is provided", () => {
    const html = render({
      record: { title: "Test Record" },
      primary: <p>main</p>,
      error: new Error("Not found"),
    });
    expect(html).toContain("Not found");
    expect(html).not.toContain(">main<");
  });
});
