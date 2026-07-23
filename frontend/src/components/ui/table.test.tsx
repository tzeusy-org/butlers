// @vitest-environment jsdom

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { TableHead } from "./table"

describe("TableHead", () => {
  it("defaults column headers to scope=col", () => {
    const html = renderToStaticMarkup(<TableHead>Butler</TableHead>)

    expect(html).toContain('scope="col"')
  })

  it("allows row headers to override the default scope", () => {
    const html = renderToStaticMarkup(<TableHead scope="row">Permission</TableHead>)

    expect(html).toContain('scope="row"')
    expect(html).not.toContain('scope="col"')
  })
})
