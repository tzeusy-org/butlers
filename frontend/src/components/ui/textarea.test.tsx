// @vitest-environment jsdom

import { createRef } from "react"
import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { Textarea } from "./textarea"

describe("Textarea", () => {
  it("forwards its ref to the native textarea element", () => {
    const ref = createRef<HTMLTextAreaElement>()

    render(<Textarea ref={ref} aria-label="Message" />)

    expect(ref.current).toBe(screen.getByRole("textbox", { name: "Message" }))
  })
})
