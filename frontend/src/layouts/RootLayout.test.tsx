// @vitest-environment jsdom

import type { ReactNode } from "react"
import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

const { mockAnnounce, mockPageHeader, mockShell, mockFloatingChatWidget } = vi.hoisted(() => ({
  mockAnnounce: vi.fn(),
  mockPageHeader: vi.fn(),
  mockShell: vi.fn(),
  mockFloatingChatWidget: vi.fn(),
}))

let mockHealth: "healthy" | "late" | "down" = "healthy"
let mockClientLinkStatus: "online" | "offline" | "reconnecting" = "online"
let mockIsXlViewport = false

import RootLayout from "./RootLayout"

vi.mock("react-router", () => ({ Outlet: () => <div data-testid="outlet" /> }))
vi.mock("../components/layout/Shell", () => ({
  default: ({
    children,
    header,
    chatDock,
  }: {
    children: ReactNode
    header: ReactNode
    chatDock?: ReactNode
  }) => {
    mockShell({ hasChatDock: chatDock != null })
    return (
      <>
        {header}
        {children}
        {chatDock}
      </>
    )
  },
}))
vi.mock("../hooks/use-media-query", () => ({
  useMediaQuery: () => mockIsXlViewport,
}))
vi.mock("../components/chat/ChatDock", () => ({
  ChatDock: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="chat-dock-mock">
      <button onClick={onClose}>Collapse</button>
    </div>
  ),
}))
vi.mock("../components/layout/PageHeader", () => ({
  default: ({ liveStatus, clientLink }: { liveStatus?: string; clientLink?: string }) => {
    mockPageHeader(liveStatus, clientLink)
    return null
  },
}))
vi.mock("../hooks/use-client-link", () => ({
  useClientLink: () => ({ status: mockClientLinkStatus }),
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
vi.mock("../components/chat/FloatingChatWidget", () => ({
  FloatingChatWidget: (props: { onExpandDock?: () => void }) => {
    mockFloatingChatWidget(props)
    return <div data-testid="floating-chat-widget-mock" />
  },
}))
vi.mock("../lib/shell-announcer", () => ({
  announce: mockAnnounce,
  useShellAnnouncement: () => "",
}))

afterEach(() => {
  cleanup()
  mockAnnounce.mockClear()
  mockPageHeader.mockClear()
  mockShell.mockClear()
  mockFloatingChatWidget.mockClear()
  mockHealth = "healthy"
  mockClientLinkStatus = "online"
  mockIsXlViewport = false
  window.localStorage.clear()
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
    expect(mockPageHeader).toHaveBeenLastCalledWith("healthy", "online")
    expect(mockAnnounce).not.toHaveBeenCalled()

    mockHealth = "late"
    rerender(<RootLayout />)

    expect(mockPageHeader).toHaveBeenLastCalledWith("late", "online")
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

  it("propagates the client link status to the shell indicator", () => {
    mockClientLinkStatus = "offline"
    render(<RootLayout />)
    expect(mockPageHeader).toHaveBeenLastCalledWith("healthy", "offline")
  })

  it("blames the client, not the fleet, when navigator.onLine drops the socket, and stays silent on recovery", () => {
    const { rerender } = render(<RootLayout />)
    expect(mockPageHeader).toHaveBeenLastCalledWith("healthy", "online")

    // The socket closing and the client link dropping arrive together, as
    // they would from a real dropped LTE link.
    mockHealth = "down"
    mockClientLinkStatus = "offline"
    rerender(<RootLayout />)

    expect(mockAnnounce).toHaveBeenCalledWith("Your connection is offline")
    expect(mockAnnounce).not.toHaveBeenCalledWith("Fleet event stream offline")

    // Reconnect: silent recovery -- the client link itself coming back stays
    // quiet (the fleet stream is still reported down here, unrelated to the
    // socket reconnecting, so its edge does not fire either).
    mockAnnounce.mockClear()
    mockClientLinkStatus = "online"
    rerender(<RootLayout />)

    expect(mockAnnounce).not.toHaveBeenCalled()
  })

  it("still announces a genuine fleet outage when the client link stays online", () => {
    const { rerender } = render(<RootLayout />)

    mockHealth = "down"
    rerender(<RootLayout />)

    expect(mockAnnounce).toHaveBeenCalledWith("Fleet event stream offline")
  })
})

describe("RootLayout — chat posture host (bu-0ynlk.11)", () => {
  it("docks the chat rail into Shell and hides the popover at >= xl", () => {
    mockIsXlViewport = true
    render(<RootLayout />)

    expect(mockShell).toHaveBeenLastCalledWith({ hasChatDock: true })
    expect(screen.getByTestId("chat-dock-mock")).toBeTruthy()
    expect(screen.queryByTestId("floating-chat-widget-mock")).toBeNull()
  })

  it("falls back to the popover with no dock below xl", () => {
    mockIsXlViewport = false
    render(<RootLayout />)

    expect(mockShell).toHaveBeenLastCalledWith({ hasChatDock: false })
    expect(screen.queryByTestId("chat-dock-mock")).toBeNull()
    expect(screen.getByTestId("floating-chat-widget-mock")).toBeTruthy()
  })

  it("collapsing the dock persists the choice and falls back to the popover", () => {
    mockIsXlViewport = true
    render(<RootLayout />)
    expect(screen.getByTestId("chat-dock-mock")).toBeTruthy()

    act(() => {
      screen.getByText("Collapse").click()
    })

    expect(screen.queryByTestId("chat-dock-mock")).toBeNull()
    expect(screen.getByTestId("floating-chat-widget-mock")).toBeTruthy()
    expect(window.localStorage.getItem("butlers.chat-dock-open")).toBe("false")
  })

  it("the popover trigger reopens the dock (not itself) at xl once collapsed", () => {
    window.localStorage.setItem("butlers.chat-dock-open", "false")
    mockIsXlViewport = true
    render(<RootLayout />)

    expect(screen.queryByTestId("chat-dock-mock")).toBeNull()
    const { onExpandDock } = mockFloatingChatWidget.mock.lastCall![0]
    expect(onExpandDock).toBeInstanceOf(Function)

    act(() => onExpandDock())

    expect(screen.getByTestId("chat-dock-mock")).toBeTruthy()
    expect(window.localStorage.getItem("butlers.chat-dock-open")).toBe("true")
  })

  it("does not offer onExpandDock below xl (the popover is the only posture there)", () => {
    mockIsXlViewport = false
    render(<RootLayout />)

    const { onExpandDock } = mockFloatingChatWidget.mock.lastCall![0]
    expect(onExpandDock).toBeUndefined()
  })
})
