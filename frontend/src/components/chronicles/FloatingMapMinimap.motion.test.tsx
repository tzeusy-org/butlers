// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { MapWidgetInnerProps } from "./MapWidgetInner"
import { FloatingMapMinimap } from "./FloatingMapMinimap"

const observed = vi.hoisted(() => ({ props: null as MapWidgetInnerProps | null }))
vi.mock("./MapWidget", () => ({
  MapWidget: (props: MapWidgetInnerProps) => {
    observed.props = props
    return <div data-testid="mounted-map-content" className={props.height}>Map content</div>
  },
}))
afterEach(cleanup)

it("keeps mode controls keyboard-operable and preserves live map content and forwarded props", async () => {
  const user = userEvent.setup()
  const trailPoints = [{ lng: 10, lat: 20 }]
  const playheadPoint = { lng: 11, lat: 21 }
  render(<FloatingMapMinimap trailPoints={trailPoints} playheadPoint={playheadPoint} />)
  expect(observed.props).toEqual({ points: [], trailPoints, playheadPoint, height: "h-full" })
  const toggle = screen.getByRole("button", { name: "Expand map" })
  toggle.focus()
  await user.keyboard("{Enter}")
  expect(screen.getByRole("region", { name: "Location map" }).getAttribute("data-mode")).toBe("expanded")
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Shrink map" }))
  await user.keyboard(" ")
  expect(screen.getByRole("region", { name: "Location map" }).getAttribute("data-mode")).toBe("open")
  await user.click(screen.getByRole("button", { name: "Minimize map" }))
  expect(screen.queryByTestId("mounted-map-content")).toBeNull()
  const restore = screen.getByRole("button", { name: "Show map" })
  restore.focus()
  await user.keyboard("{Enter}")
  expect(screen.getByTestId("mounted-map-content").textContent).toBe("Map content")
  expect(screen.getByRole("region", { name: "Location map" }).getAttribute("data-mode")).toBe("open")
  expect(observed.props!.trailPoints).toBe(trailPoints)
})
