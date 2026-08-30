// @vitest-environment jsdom

import type { ReactNode } from "react"
import { render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

const { mockAnnounce, mockPageHeader } = vi.hoisted(() => ({
  mockAnnounce: vi.fn(),
  mockPageHeader: vi.fn(),
}))

let mockHealth: "healthy" | "late" | "down" = "healthy"

import RootLayout from "./RootLayout"

vi.mock("react-router", () => ({ Outlet: () => <div data-testid="outlet" /> }))
vi.mock("../components/layout/Shell", () => ({
  default: ({ children, header }: { children: ReactNode; header: ReactNode }) => <>{header}{children}</>,
}))
vi.mock("../components/layout/PageHeader", () => ({
  default: ({ liveStatus }: { liveStatus?: string }) => {
    mockPageHeader(liveStatus)
    return null
  },
}))
vi.mock("../components/layout/EntityFinder", () => ({ default: () => null }))
vi.mock("../components/layout/GlobalActionsRegistrar", () => ({ GlobalActionsRegistrar: () => null }))
vi.mock("../components/ErrorBoundary", () => ({
  ErrorBoundary: ({ children }: { children: ReactNode }) => <>{children}</>,
}))
vi.mock("../components/ui/sonner", () => ({ Toaster: () => null }))
vi.mock("../components/ui/breadcrumbs-control", () => ({
  BreadcrumbsControlProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}))
vi.mock("../lib/command-registry", () => ({
  CommandRegistryProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}))
vi.mock("../hooks/use-register-shortcut", () => ({
  ShortcutRegistryProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}))
vi.mock("../lib/page-context", () => ({
  PageContextProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}))
vi.mock("../hooks/use-keyboard-shortcuts", () => ({ useKeyboardShortcuts: () => undefined }))
vi.mock("../components/ui/shortcut-hints", () => ({ ShortcutHints: () => null }))
vi.mock("../lib/event-bus", () => ({
  EventBusProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useEventBus: () => ({
    status: "open",
    health: mockHealth,
    lastEventAt: null,
    subscribe: vi.fn(),
  }),
}))
vi.mock("../components/chat/FloatingChatWidget", () => ({ FloatingChatWidget: () => null }))
vi.mock("../lib/shell-announcer", () => ({
  announce: mockAnnounce,
  useShellAnnouncement: () => "",
}))

afterEach(() => {
  mockAnnounce.mockClear()
  mockPageHeader.mockClear()
  mockHealth = "healthy"
})

describe("RootLayout", () => {
  it("renders a visible-on-focus skip link before routed content", () => {
    render(<RootLayout />)

    const skipLink = screen.getByRole("link", { name: "Skip to main content" })
    expect(skipLink.getAttribute("href")).toBe("#main-content")
    expect(skipLink.className).toContain("sr-only")
    expect(skipLink.className).toContain("focus:not-sr-only")
  })

  it("propagates late shared health to the shell indicator and announcement", () => {
    const { rerender } = render(<RootLayout />)
    expect(mockPageHeader).toHaveBeenLastCalledWith("healthy")
    expect(mockAnnounce).not.toHaveBeenCalled()

    mockHealth = "late"
    rerender(<RootLayout />)

    expect(mockPageHeader).toHaveBeenLastCalledWith("late")
    expect(mockAnnounce).toHaveBeenCalledWith("Fleet event stream reconnecting")
  })

  it("does not announce the cold-start late state but announces a later reconnect", () => {
    mockHealth = "down"
    const { rerender } = render(<RootLayout />)

    mockHealth = "late"
    rerender(<RootLayout />)
    expect(mockAnnounce).not.toHaveBeenCalled()

    mockHealth = "healthy"
    rerender(<RootLayout />)
    expect(mockAnnounce).not.toHaveBeenCalled()

    mockHealth = "late"
    rerender(<RootLayout />)
    expect(mockAnnounce).toHaveBeenCalledOnce()
    expect(mockAnnounce).toHaveBeenCalledWith("Fleet event stream reconnecting")
  })
})
