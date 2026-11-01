// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { MapPanContext, useMapPanContextValue, useMapPanTo } from "@/components/workspace/map-pan-store"
import { MapWidgetInner, type MapWidgetInnerProps } from "./MapWidgetInner"

const calls = vi.hoisted(() => ({
  flyTo: vi.fn(), fitBounds: vi.fn(), remove: vi.fn(), marker: vi.fn(),
  extend: vi.fn(), styleReady: true, addSource: vi.fn(),
}))
vi.mock("maplibre-gl", () => {
  class Map {
    flyTo = calls.flyTo
    fitBounds = calls.fitBounds
    remove = calls.remove
    addSource = calls.addSource
    isStyleLoaded() { return calls.styleReady }
    setStyle() {}
    addLayer() {}
    getSource() { return { setData() {} } }
    getLayer() { return undefined }
    on() {}
    off() {}
  }
  class Marker {
    setLngLat(point: unknown) { calls.marker(point); return this }
    addTo() { return this }
    setPopup() { return this }
    remove() {}
  }
  class Popup { setText() { return this } }
  class LngLatBounds { extend(point: unknown) { calls.extend(point); return this } }
  return { default: { Map, Marker, Popup, LngLatBounds } }
})
vi.mock("maplibre-gl/dist/maplibre-gl.css", () => ({}))

function Trigger() {
  const pan = useMapPanTo()
  return <button onClick={() => pan(20, 10)}>Show location</button>
}
function Harness(props: MapWidgetInnerProps & { showMap?: boolean }) {
  const value = useMapPanContextValue()
  return <MapPanContext.Provider value={value}>
    <Trigger />
    {props.showMap !== false && <MapWidgetInner {...props} />}
  </MapPanContext.Provider>
}
afterEach(() => { cleanup(); vi.clearAllMocks(); calls.styleReady = true })

describe("MapWidget instant camera navigation", () => {
  it("keeps marker fitting and requested destinations instant, filters sensitive points, and cleans up", () => {
    const points = [{ lng: 10, lat: 20, label: "Allowed place" }, { lng: 11, lat: 21, privacy_tier: "sensitive" }]
    const view = render(<Harness points={points} />)
    expect(calls.extend).toHaveBeenCalledExactlyOnceWith([10, 20])
    expect(calls.marker).toHaveBeenCalledExactlyOnceWith([10, 20])
    expect(calls.fitBounds).toHaveBeenCalledWith(expect.anything(), { padding: 48, maxZoom: 14, duration: 0 })
    fireEvent.click(screen.getByRole("button", { name: "Show location" }))
    expect(calls.flyTo).toHaveBeenCalledExactlyOnceWith({ center: [10, 20], zoom: 13, duration: 0 })
    view.rerender(<Harness points={points} showMap={false} />)
    expect(calls.remove).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole("button", { name: "Show location" }))
    expect(calls.flyTo).toHaveBeenCalledTimes(1)
  })

  it("retains instant trail fitting, repeated updates and safe empty or standalone behavior", () => {
    const trail = [{ lng: 10, lat: 20 }, { lng: 11, lat: 21 }]
    calls.styleReady = false
    const view = render(<Harness points={[]} trailPoints={trail} />)
    expect(calls.addSource).not.toHaveBeenCalled()
    expect(calls.fitBounds).toHaveBeenLastCalledWith(expect.anything(), { padding: 48, maxZoom: 14, duration: 0 })
    view.rerender(<Harness points={[]} trailPoints={[...trail, { lng: 12, lat: 22 }]} />)
    expect(calls.fitBounds).toHaveBeenCalledTimes(2)
    expect(calls.extend).toHaveBeenLastCalledWith([12, 22])
    expect(calls.remove).not.toHaveBeenCalled()
    view.rerender(<Harness points={[]} />)
    expect(calls.remove).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole("button", { name: "Show location" }))
    expect(calls.flyTo).not.toHaveBeenCalled()
    view.unmount()
    render(<><Trigger /><MapWidgetInner points={[]} /></>)
    fireEvent.click(screen.getByRole("button", { name: "Show location" }))
    expect(calls.flyTo).not.toHaveBeenCalled()
    expect(calls.fitBounds).toHaveBeenCalledTimes(2)
  })
})
