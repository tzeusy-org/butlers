import { type ReactNode, useState } from 'react'
import Sidebar from './Sidebar'
import {
  Sheet,
  SheetContent,
  SheetTitle,
} from '../ui/sheet'

interface ShellProps {
  header: ReactNode
  children: ReactNode
}

export default function Shell({ header, children }: ShellProps) {
  const [desktopCollapsed, setDesktopCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Mobile sidebar (Sheet/drawer) — only rendered below md */}
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="w-64 p-0 md:hidden" showCloseButton={false}>
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <Sidebar onNavClick={() => setMobileOpen(false)} />
        </SheetContent>
      </Sheet>

      {/* Desktop sidebar — width switches instantly (no transition) so no
          layout property is animated. Brand text fades via transition-opacity
          in Sidebar.tsx; nav labels use conditional rendering. */}
      <aside
        className={`hidden md:flex md:flex-col border-r border-border ${
          desktopCollapsed ? 'md:w-16' : 'md:w-64'
        }`}
      >
        <Sidebar
          collapsed={desktopCollapsed}
          onToggleCollapse={() => setDesktopCollapsed((prev) => !prev)}
        />
      </aside>

      {/* Main area */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Header */}
        <header className="flex h-14 items-center border-b border-border px-6">
          {/* Mobile hamburger button — only visible below md */}
          <button
            className="mr-3 flex items-center justify-center rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent/50 hover:text-accent-foreground md:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Open navigation menu"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="4" x2="20" y1="12" y2="12" />
              <line x1="4" x2="20" y1="6" y2="6" />
              <line x1="4" x2="20" y1="18" y2="18" />
            </svg>
          </button>
          {header}
        </header>

        {/* Content */}
        <main className="flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  )
}
