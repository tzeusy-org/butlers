import { describe, expect, it } from "vitest"

import { manualChunks } from "./vite.config"

describe("manualChunks", () => {
  it("groups the explicit package domains", () => {
    expect(manualChunks("/project/node_modules/maplibre-gl/dist/maplibre-gl.js")).toBe("vendor-map")
    expect(manualChunks("/project/node_modules/@radix-ui/react-dialog/dist/index.mjs")).toBe("vendor-ui")
    expect(manualChunks("/project/node_modules/.pnpm/react@18/node_modules/react/index.js")).toBe("vendor-framework")
  })

  it("does not match a nested path inside another package", () => {
    expect(manualChunks("/project/node_modules/future-widget/dist/react/index.js")).toBeUndefined()
  })
})
