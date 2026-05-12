// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// atoms.test.tsx — unit tests for shared butler-detail atom primitives
// (bu-iuol4.13, bu-hdavr.3)
//
// Coverage:
//   ButlerPanelGrid — canonical container classes, className merge, data-testid
//   MonoLabel       — color prop (all tones)
//   Panel           — span 1/2/3/4 (responsive), scroll prop, height prop, accent flag
//   KpiCell         — tone variants (amber/red/green/dim/fg), big vs default size
//   KV              — mono prop
//   ErrorLine       — renders children, icon, destructive tone, data-testid
//   LoadingLine     — renders loading text, data-testid
//   EmptyLine       — renders children, serif italic, data-testid
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { ButlerPanelGrid, MonoLabel, Panel, KpiCell, KV, ErrorLine, LoadingLine, EmptyLine } from "./atoms"

// ---------------------------------------------------------------------------
// ButlerPanelGrid
// ---------------------------------------------------------------------------

describe("ButlerPanelGrid", () => {
  it("renders children", () => {
    const html = renderToStaticMarkup(<ButlerPanelGrid><div>child</div></ButlerPanelGrid>)
    expect(html).toContain("child")
  })

  it("includes grid class", () => {
    const html = renderToStaticMarkup(<ButlerPanelGrid><span /></ButlerPanelGrid>)
    expect(html).toContain("grid")
  })

  it("includes grid-cols-1 as base", () => {
    const html = renderToStaticMarkup(<ButlerPanelGrid><span /></ButlerPanelGrid>)
    expect(html).toContain("grid-cols-1")
  })

  it("includes lg:grid-cols-4 for the 4-column layout", () => {
    const html = renderToStaticMarkup(<ButlerPanelGrid><span /></ButlerPanelGrid>)
    expect(html).toContain("lg:grid-cols-4")
  })

  it("includes border-t and border-l frame classes", () => {
    const html = renderToStaticMarkup(<ButlerPanelGrid><span /></ButlerPanelGrid>)
    expect(html).toContain("border-t")
    expect(html).toContain("border-l")
  })

  it("includes border-border/60 token", () => {
    const html = renderToStaticMarkup(<ButlerPanelGrid><span /></ButlerPanelGrid>)
    expect(html).toContain("border-border/60")
  })

  it("forwards data-testid to the outer div", () => {
    const html = renderToStaticMarkup(
      <ButlerPanelGrid data-testid="my-grid"><span /></ButlerPanelGrid>
    )
    expect(html).toContain('data-testid="my-grid"')
  })

  it("merges additional className", () => {
    const html = renderToStaticMarkup(
      <ButlerPanelGrid className="sm:grid-cols-2 md:grid-cols-4"><span /></ButlerPanelGrid>
    )
    expect(html).toContain("sm:grid-cols-2")
    expect(html).toContain("md:grid-cols-4")
    expect(html).toContain("lg:grid-cols-4")
  })

  it("does not contain raw oklch or hex", () => {
    const html = renderToStaticMarkup(<ButlerPanelGrid><span /></ButlerPanelGrid>)
    expect(html).not.toMatch(/oklch/)
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,6}/)
  })
})

// ---------------------------------------------------------------------------
// MonoLabel
// ---------------------------------------------------------------------------

describe("MonoLabel", () => {
  it("renders children", () => {
    const html = renderToStaticMarkup(<MonoLabel>SESSIONS</MonoLabel>)
    expect(html).toContain("SESSIONS")
  })

  it("defaults to dim (text-muted-foreground)", () => {
    const html = renderToStaticMarkup(<MonoLabel>X</MonoLabel>)
    expect(html).toContain("text-muted-foreground")
  })

  it("color=amber maps to text-amber-500", () => {
    const html = renderToStaticMarkup(<MonoLabel color="amber">A</MonoLabel>)
    expect(html).toContain("text-amber-500")
  })

  it("color=red maps to text-destructive", () => {
    const html = renderToStaticMarkup(<MonoLabel color="red">R</MonoLabel>)
    expect(html).toContain("text-destructive")
  })

  it("color=green maps to text-emerald-500", () => {
    const html = renderToStaticMarkup(<MonoLabel color="green">G</MonoLabel>)
    expect(html).toContain("text-emerald-500")
  })

  it("color=dim maps to text-muted-foreground", () => {
    const html = renderToStaticMarkup(<MonoLabel color="dim">D</MonoLabel>)
    expect(html).toContain("text-muted-foreground")
  })

  it("color=fg maps to text-foreground", () => {
    const html = renderToStaticMarkup(<MonoLabel color="fg">F</MonoLabel>)
    expect(html).toContain("text-foreground")
  })

  it("includes font-mono and uppercase classes", () => {
    const html = renderToStaticMarkup(<MonoLabel>L</MonoLabel>)
    expect(html).toContain("font-mono")
    expect(html).toContain("uppercase")
    expect(html).toContain("tracking-wider")
  })

  it("passes through additional className", () => {
    const html = renderToStaticMarkup(<MonoLabel className="custom-cls">T</MonoLabel>)
    expect(html).toContain("custom-cls")
  })

  it("does not contain raw oklch or hex color", () => {
    const html = renderToStaticMarkup(<MonoLabel color="amber">T</MonoLabel>)
    expect(html).not.toMatch(/oklch/)
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,6}/)
  })
})

// ---------------------------------------------------------------------------
// Panel — span (responsive)
//
// span=1 stays col-span-1 only (no lg: prefix needed — already mobile-first).
// span=2/3/4 use col-span-1 as base + lg:col-span-X to avoid forcing implicit
// multi-column layout on viewports narrower than lg (1024px).
// ---------------------------------------------------------------------------

describe("Panel: span", () => {
  it("span=1 renders col-span-1 only (no lg: prefix)", () => {
    const html = renderToStaticMarkup(<Panel span={1}>body</Panel>)
    expect(html).toContain("col-span-1")
    expect(html).not.toContain("lg:col-span-1")
  })

  it("span=2 renders col-span-1 base and lg:col-span-2", () => {
    const html = renderToStaticMarkup(<Panel span={2}>body</Panel>)
    expect(html).toContain("col-span-1")
    expect(html).toContain("lg:col-span-2")
    expect(html).not.toContain("col-span-2 ")  // no bare col-span-2 (only lg-prefixed)
  })

  it("span=3 renders col-span-1 base and lg:col-span-3", () => {
    const html = renderToStaticMarkup(<Panel span={3}>body</Panel>)
    expect(html).toContain("col-span-1")
    expect(html).toContain("lg:col-span-3")
  })

  it("span=4 renders col-span-1 base and lg:col-span-4", () => {
    const html = renderToStaticMarkup(<Panel span={4}>body</Panel>)
    expect(html).toContain("col-span-1")
    expect(html).toContain("lg:col-span-4")
  })

  it("defaults to col-span-1 when span is omitted", () => {
    const html = renderToStaticMarkup(<Panel>body</Panel>)
    expect(html).toContain("col-span-1")
  })
})

// ---------------------------------------------------------------------------
// Panel — scroll prop
// ---------------------------------------------------------------------------

describe("Panel: scroll prop", () => {
  it("scroll=true adds overflow-y-auto on the body", () => {
    const html = renderToStaticMarkup(<Panel scroll>body</Panel>)
    expect(html).toContain("overflow-y-auto")
  })

  it("scroll=false (default) does not include overflow-y-auto", () => {
    const html = renderToStaticMarkup(<Panel>body</Panel>)
    expect(html).not.toContain("overflow-y-auto")
  })
})

// ---------------------------------------------------------------------------
// Panel — height prop (typed-primitive exemption: uses inline style)
// ---------------------------------------------------------------------------

describe("Panel: height prop", () => {
  it("height prop applies inline style on the body div", () => {
    const html = renderToStaticMarkup(<Panel height="320px">body</Panel>)
    expect(html).toContain('style="height:320px"')
  })

  it("no height prop produces no inline style", () => {
    const html = renderToStaticMarkup(<Panel>body</Panel>)
    // No inline style at all on the body div (Panel wrapper is allowed style-free).
    // Check that we don't set height style anywhere.
    expect(html).not.toContain("height:")
  })
})

// ---------------------------------------------------------------------------
// Panel — accent flag
// ---------------------------------------------------------------------------

describe("Panel: accent flag", () => {
  it("accent=true renders the accent stripe with bg-primary", () => {
    const html = renderToStaticMarkup(<Panel accent>body</Panel>)
    expect(html).toContain("bg-primary")
  })

  it("accent=false (default) does not render bg-primary stripe", () => {
    const html = renderToStaticMarkup(<Panel>body</Panel>)
    expect(html).not.toContain("bg-primary")
  })

  it("accent stripe has aria-hidden", () => {
    const html = renderToStaticMarkup(<Panel accent>body</Panel>)
    expect(html).toContain('aria-hidden="true"')
  })

  it("does not render inline oklch or hex in accent stripe", () => {
    const html = renderToStaticMarkup(<Panel accent>body</Panel>)
    expect(html).not.toMatch(/oklch/)
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,6}/)
  })
})

// ---------------------------------------------------------------------------
// Panel — title / sub eyebrow
// ---------------------------------------------------------------------------

describe("Panel: title and sub", () => {
  it("renders title when provided", () => {
    const html = renderToStaticMarkup(<Panel title="METRICS">body</Panel>)
    expect(html).toContain("METRICS")
  })

  it("renders sub when provided alongside title", () => {
    const html = renderToStaticMarkup(<Panel title="METRICS" sub="(live)">body</Panel>)
    expect(html).toContain("METRICS")
    expect(html).toContain("(live)")
  })

  it("does not render header section when title is omitted", () => {
    const html = renderToStaticMarkup(<Panel>body</Panel>)
    // border-b on the header is only present when a title is rendered
    expect(html).not.toContain("border-b border-border/40")
  })

  it("renders children in the body", () => {
    const html = renderToStaticMarkup(<Panel title="T">hello world</Panel>)
    expect(html).toContain("hello world")
  })
})

// ---------------------------------------------------------------------------
// KpiCell — tone variants
// ---------------------------------------------------------------------------

describe("KpiCell: tone variants", () => {
  it("tone=amber maps to text-amber-500", () => {
    const html = renderToStaticMarkup(<KpiCell label="COST" value="$1.23" tone="amber" />)
    expect(html).toContain("text-amber-500")
  })

  it("tone=red maps to text-destructive", () => {
    const html = renderToStaticMarkup(<KpiCell label="ERRORS" value="3" tone="red" />)
    expect(html).toContain("text-destructive")
  })

  it("tone=green maps to text-emerald-500", () => {
    const html = renderToStaticMarkup(<KpiCell label="SESSIONS" value="12" tone="green" />)
    expect(html).toContain("text-emerald-500")
  })

  it("tone=dim maps to text-muted-foreground", () => {
    const html = renderToStaticMarkup(<KpiCell label="IDLE" value="—" tone="dim" />)
    expect(html).toContain("text-muted-foreground")
  })

  it("tone=fg maps to text-foreground", () => {
    const html = renderToStaticMarkup(<KpiCell label="LOAD" value="50%" tone="fg" />)
    expect(html).toContain("text-foreground")
  })

  it("defaults to fg (text-foreground) when tone is omitted", () => {
    const html = renderToStaticMarkup(<KpiCell label="SESSIONS" value="5" />)
    expect(html).toContain("text-foreground")
  })
})

// ---------------------------------------------------------------------------
// KpiCell — big vs default font size
// ---------------------------------------------------------------------------

describe("KpiCell: big vs default size", () => {
  it("big=true renders text-[28px]", () => {
    const html = renderToStaticMarkup(<KpiCell label="COST" value="$4.50" big />)
    expect(html).toContain("text-[28px]")
    expect(html).not.toContain("text-[22px]")
  })

  it("big=false (default) renders text-[22px]", () => {
    const html = renderToStaticMarkup(<KpiCell label="COST" value="$4.50" />)
    expect(html).toContain("text-[22px]")
    expect(html).not.toContain("text-[28px]")
  })
})

// ---------------------------------------------------------------------------
// KpiCell — label and value rendering
// ---------------------------------------------------------------------------

describe("KpiCell: content", () => {
  it("renders label text", () => {
    const html = renderToStaticMarkup(<KpiCell label="SPEND TODAY" value="$1.00" />)
    expect(html).toContain("SPEND TODAY")
  })

  it("renders value", () => {
    const html = renderToStaticMarkup(<KpiCell label="X" value="42" />)
    expect(html).toContain("42")
  })

  it("renders sub when provided", () => {
    const html = renderToStaticMarkup(<KpiCell label="X" value="5" sub="last 7 days" />)
    expect(html).toContain("last 7 days")
  })

  it("does not render sub element when sub is omitted", () => {
    const html = renderToStaticMarkup(<KpiCell label="X" value="5" />)
    // The sub span carries leading-tight; its absence can be verified by checking
    // the total rendered content does not contain a spurious sub span.
    expect(html).not.toContain("leading-tight")
  })

  it("uses font-mono and tnum on the value span", () => {
    const html = renderToStaticMarkup(<KpiCell label="X" value="5" />)
    expect(html).toContain("font-mono")
    expect(html).toContain("tnum")
  })

  it("does not render raw oklch or hex", () => {
    const html = renderToStaticMarkup(
      <KpiCell label="X" value="5" tone="amber" big />,
    )
    expect(html).not.toMatch(/oklch/)
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,6}/)
  })
})

// ---------------------------------------------------------------------------
// KV
// ---------------------------------------------------------------------------

describe("KV", () => {
  it("renders the key label", () => {
    const html = renderToStaticMarkup(<KV k="container" v="general-butler" />)
    expect(html).toContain("container")
  })

  it("renders the value", () => {
    const html = renderToStaticMarkup(<KV k="container" v="general-butler" />)
    expect(html).toContain("general-butler")
  })

  it("mono=true adds font-mono and tnum to the value span", () => {
    const html = renderToStaticMarkup(<KV k="path" v="/etc/config.toml" mono />)
    expect(html).toContain("font-mono")
    expect(html).toContain("tnum")
  })

  it("mono=false (default) does not add font-mono to the value span", () => {
    // The key span does not use font-mono; value should not either when mono=false.
    const html = renderToStaticMarkup(<KV k="name" v="general" />)
    // font-mono should NOT appear on the value when mono=false.
    // (MonoLabel in Panel headers use font-mono but KV value does not when mono=false)
    expect(html).not.toContain("font-mono")
  })

  it("does not render raw oklch or hex", () => {
    const html = renderToStaticMarkup(<KV k="k" v="v" mono />)
    expect(html).not.toMatch(/oklch/)
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,6}/)
  })

  it("passes through className to the row", () => {
    const html = renderToStaticMarkup(<KV k="k" v="v" className="custom-row" />)
    expect(html).toContain("custom-row")
  })
})

// ---------------------------------------------------------------------------
// ErrorLine
// ---------------------------------------------------------------------------

describe("ErrorLine", () => {
  it("renders children text", () => {
    const html = renderToStaticMarkup(<ErrorLine>Could not load data.</ErrorLine>)
    expect(html).toContain("Could not load data.")
  })

  it("applies text-destructive", () => {
    const html = renderToStaticMarkup(<ErrorLine>Error</ErrorLine>)
    expect(html).toContain("text-destructive")
  })

  it("sets data-testid=error-state-line", () => {
    const html = renderToStaticMarkup(<ErrorLine>Error</ErrorLine>)
    expect(html).toContain('data-testid="error-state-line"')
  })

  it("includes flex layout classes for icon alignment", () => {
    const html = renderToStaticMarkup(<ErrorLine>Error</ErrorLine>)
    expect(html).toContain("flex")
    expect(html).toContain("items-center")
    expect(html).toContain("gap-1.5")
  })

  it("wraps children in a truncate span", () => {
    const html = renderToStaticMarkup(<ErrorLine>Error</ErrorLine>)
    expect(html).toContain("truncate")
  })

  it("passes through className", () => {
    const html = renderToStaticMarkup(<ErrorLine className="custom-err">Error</ErrorLine>)
    expect(html).toContain("custom-err")
  })

  it("does not render raw oklch or hex", () => {
    const html = renderToStaticMarkup(<ErrorLine>Error</ErrorLine>)
    expect(html).not.toMatch(/oklch/)
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,6}/)
  })
})

// ---------------------------------------------------------------------------
// LoadingLine
// ---------------------------------------------------------------------------

describe("LoadingLine", () => {
  it("renders 'Loading...' text", () => {
    const html = renderToStaticMarkup(<LoadingLine />)
    expect(html).toContain("Loading...")
  })

  it("sets data-testid=loading-line", () => {
    const html = renderToStaticMarkup(<LoadingLine />)
    expect(html).toContain('data-testid="loading-line"')
  })

  it("applies text-muted-foreground", () => {
    const html = renderToStaticMarkup(<LoadingLine />)
    expect(html).toContain("text-muted-foreground")
  })

  it("applies text-sm", () => {
    const html = renderToStaticMarkup(<LoadingLine />)
    expect(html).toContain("text-sm")
  })

  it("passes through className", () => {
    const html = renderToStaticMarkup(<LoadingLine className="my-loading" />)
    expect(html).toContain("my-loading")
  })

  it("does not render raw oklch or hex", () => {
    const html = renderToStaticMarkup(<LoadingLine />)
    expect(html).not.toMatch(/oklch/)
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,6}/)
  })
})

// ---------------------------------------------------------------------------
// EmptyLine
// ---------------------------------------------------------------------------

describe("EmptyLine", () => {
  it("renders children text", () => {
    const html = renderToStaticMarkup(<EmptyLine>No data yet.</EmptyLine>)
    expect(html).toContain("No data yet.")
  })

  it("sets data-testid=empty-state-line", () => {
    const html = renderToStaticMarkup(<EmptyLine>Nothing here.</EmptyLine>)
    expect(html).toContain('data-testid="empty-state-line"')
  })

  it("applies text-muted-foreground", () => {
    const html = renderToStaticMarkup(<EmptyLine>Nothing.</EmptyLine>)
    expect(html).toContain("text-muted-foreground")
  })

  it("applies italic", () => {
    const html = renderToStaticMarkup(<EmptyLine>Nothing.</EmptyLine>)
    expect(html).toContain("italic")
  })

  it("applies serif font-family utility", () => {
    const html = renderToStaticMarkup(<EmptyLine>Nothing.</EmptyLine>)
    expect(html).toContain("font-serif")
  })

  it("passes through className", () => {
    const html = renderToStaticMarkup(<EmptyLine className="my-empty">Empty.</EmptyLine>)
    expect(html).toContain("my-empty")
  })

  it("does not render raw oklch or hex", () => {
    const html = renderToStaticMarkup(<EmptyLine>Nothing.</EmptyLine>)
    expect(html).not.toMatch(/oklch/)
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,6}/)
  })
})
