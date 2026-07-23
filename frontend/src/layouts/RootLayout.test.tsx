// @vitest-environment jsdom

import type { ReactNode } from "react"
import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import RootLayout from "./RootLayout"

vi.mock("react-router", () => ({ Outlet: () => <div data-testid="outlet" /> }))
vi.mock("../components/layout/Shell", () => ({
  default: ({ children }: { children: ReactNode }) => <>{children}</>,
}))
vi.mock("../components/layout/PageHeader", () => ({ default: () => null }))
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
  useEventBus: () => ({ status: "open" }),
}))
vi.mock("../components/chat/FloatingChatWidget", () => ({ FloatingChatWidget: () => null }))
vi.mock("../lib/shell-announcer", () => ({
  announce: () => undefined,
  useShellAnnouncement: () => "",
}))

describe("RootLayout", () => {
  it("renders a visible-on-focus skip link before routed content", () => {
    render(<RootLayout />)

    const skipLink = screen.getByRole("link", { name: "Skip to main content" })
    expect(skipLink.getAttribute("href")).toBe("#main-content")
    expect(skipLink.className).toContain("sr-only")
    expect(skipLink.className).toContain("focus:not-sr-only")
  })
})
