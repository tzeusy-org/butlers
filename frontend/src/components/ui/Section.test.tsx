import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import {
  Section,
  SectionAction,
  SectionContent,
  SectionDescription,
  SectionFooter,
  SectionHeader,
  SectionTitle,
} from "./Section"

describe("Section", () => {
  it("renders a semantic section with an eyebrow heading", () => {
    const html = renderToStaticMarkup(
      <Section eyebrow="Operations">
        <p>Healthy</p>
      </Section>,
    )

    expect(html).toContain("<section")
    expect(html).toContain('data-slot="section"')
    expect(html).toContain('<h2 data-slot="eyebrow"')
    expect(html).toContain(">Operations</h2>")
  })

  it("does not render card chrome", () => {
    const html = renderToStaticMarkup(<Section>Content</Section>)

    expect(html).not.toContain('data-slot="card"')
    expect(html).not.toContain("rounded-xl")
    expect(html).not.toContain("bg-card")
  })

  it("renders a quiet section as one calm empty sentence", () => {
    const html = renderToStaticMarkup(
      <Section quiet empty="Nothing waiting.">
        <p>Should not render</p>
      </Section>,
    )

    expect(html).toContain("Nothing waiting.")
    expect(html).toContain("font-serif")
    expect(html).toContain("italic")
    expect(html).not.toContain("Should not render")
  })

  it("renders children when a quiet section has no empty sentence", () => {
    const html = renderToStaticMarkup(
      <Section quiet>
        <p>Loaded content</p>
      </Section>,
    )

    expect(html).toContain("Loaded content")
  })

  it("keeps section anatomy semantic and slot-addressable", () => {
    const html = renderToStaticMarkup(
      <Section>
        <SectionHeader>
          <SectionTitle>Backups</SectionTitle>
          <SectionDescription>Artifact health</SectionDescription>
          <SectionAction>Inspect</SectionAction>
        </SectionHeader>
        <SectionContent>Rows</SectionContent>
        <SectionFooter>Footer</SectionFooter>
      </Section>,
    )

    expect(html).toContain('data-slot="section-header"')
    expect(html).toContain('data-slot="section-title"')
    expect(html).toContain('data-slot="section-description"')
    expect(html).toContain('data-slot="section-action"')
    expect(html).toContain('data-slot="section-content"')
    expect(html).toContain('data-slot="section-footer"')
  })
})
