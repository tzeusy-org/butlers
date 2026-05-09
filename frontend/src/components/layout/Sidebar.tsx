import { useState } from 'react'
import { NavLink, useLocation } from 'react-router'
import { useButlers } from '@/hooks/use-butlers'
import { useCostSummary } from '@/hooks/use-costs'
import { useBadgeCounts } from '@/hooks/use-qa-badge'
import { ButlerMark } from '@/components/ui/ButlerMark'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { navSections, type NavItem, type NavFlatItem, type NavGroupItem, type NavSection } from './nav-config'

// ---------------------------------------------------------------------------
// Type guard
// ---------------------------------------------------------------------------

function isGroup(item: NavItem): item is NavGroupItem {
  return item.kind === 'group'
}

// ---------------------------------------------------------------------------
// Helper: check if a path matches the current location
// ---------------------------------------------------------------------------

function isPathActive(pathname: string, itemPath: string, end?: boolean): boolean {
  if (end) {
    return pathname === itemPath
  }
  return pathname === itemPath || pathname.startsWith(itemPath + '/')
}

// ---------------------------------------------------------------------------
// Butler-aware filtering
// ---------------------------------------------------------------------------

interface ButlerStatusMap {
  [name: string]: string
}

function useFilteredNavSections(sections: NavSection[]): {
  sections: NavSection[]
  butlerStatusMap: ButlerStatusMap
  isLoading: boolean
  isError: boolean
} {
  const { data: response, isLoading, isError } = useButlers()

  if (isLoading || isError || !response) {
    return { sections, butlerStatusMap: {}, isLoading, isError }
  }

  const butlerNames = new Set(response.data.map((b) => b.name))
  const statusMap: ButlerStatusMap = {}
  for (const b of response.data) {
    statusMap[b.name] = b.status
  }

  const filtered = sections
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => {
        const butlerField = item.butler
        if (!butlerField) return true
        return butlerNames.has(butlerField)
      }),
    }))
    .filter((section) => section.items.length > 0)

  return { sections: filtered, butlerStatusMap: statusMap, isLoading: false, isError: false }
}

// ---------------------------------------------------------------------------
// Active item styles
// ---------------------------------------------------------------------------

function railItemClassName(isActive: boolean): string {
  return [
    'relative flex items-center justify-center w-full h-10 transition-colors',
    isActive
      ? 'border-l-2 border-sidebar-primary bg-sidebar-primary/[0.06] dark:bg-sidebar-primary/[0.06]'
      : 'border-l-2 border-transparent hover:bg-sidebar-accent/50',
  ].join(' ')
}

// ---------------------------------------------------------------------------
// Status dot (only for degraded/error butlers)
// ---------------------------------------------------------------------------

function StatusDot({ status }: { status: string | undefined }) {
  if (!status || (status !== 'degraded' && status !== 'error')) return null

  const color = status === 'error' ? 'bg-destructive' : 'bg-amber-500'
  return (
    <span
      className={`absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full ring-2 ring-background ${color}`}
      aria-hidden="true"
    />
  )
}

// ---------------------------------------------------------------------------
// Badge indicator (reauth=red, approvals=amber, default=primary)
// ---------------------------------------------------------------------------

function BadgeIndicator({
  count,
  variant,
}: {
  count: number
  variant?: 'red' | 'amber' | 'primary'
}) {
  if (count <= 0) return null

  const colorClass =
    variant === 'red'
      ? 'bg-red-500 text-white'
      : variant === 'amber'
        ? 'bg-amber-500 text-white'
        : 'bg-primary text-primary-foreground'

  return (
    <span
      className={`absolute right-1 top-1 flex h-3 min-w-3 items-center justify-center rounded-full px-0.5 text-[8px] font-semibold ${colorClass}`}
      aria-hidden="true"
    >
      {count > 99 ? '99+' : count}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Glyph: for Dedicated Butlers section items with a butler field, use
// ButlerMark. For all others, use the first-letter fallback.
// ---------------------------------------------------------------------------

function ItemGlyph({
  item,
  section,
  butlerStatus,
  badgeCounts,
}: {
  item: NavFlatItem
  section: NavSection
  butlerStatus?: string
  badgeCounts?: Record<string, number>
}) {
  const count = item.badgeKey && badgeCounts ? (badgeCounts[item.badgeKey] ?? 0) : 0
  const useButlerMark = section.title === 'Dedicated Butlers' && !!item.butler

  return (
    <span className="relative flex size-6 shrink-0 items-center justify-center">
      {useButlerMark ? (
        <ButlerMark name={item.butler!} tone="neutral" />
      ) : (
        <span className="flex size-6 items-center justify-center rounded text-xs font-semibold text-muted-foreground">
          {item.label[0]}
        </span>
      )}
      {/* Badge takes precedence over status dot when both would render */}
      {count > 0 ? (
        <BadgeIndicator
          count={count}
          variant={item.badgeVariant}
        />
      ) : (
        <StatusDot status={butlerStatus} />
      )}
    </span>
  )
}

// ---------------------------------------------------------------------------
// FlatNavLink — single icon rail item with tooltip
// ---------------------------------------------------------------------------

function FlatNavLink({
  item,
  section,
  onNavClick,
  butlerStatusMap,
  badgeCounts,
}: {
  item: NavFlatItem
  section: NavSection
  onNavClick?: () => void
  butlerStatusMap?: ButlerStatusMap
  badgeCounts?: Record<string, number>
}) {
  const butlerStatus = item.butler ? butlerStatusMap?.[item.butler] : undefined

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <NavLink
          to={item.path}
          end={item.end}
          onClick={onNavClick}
          className={({ isActive }) => railItemClassName(isActive)}
          aria-label={item.tooltip ?? item.label}
        >
          <ItemGlyph
            item={item}
            section={section}
            butlerStatus={butlerStatus}
            badgeCounts={badgeCounts}
          />
        </NavLink>
      </TooltipTrigger>
      <TooltipContent side="right" sideOffset={8}>
        {item.tooltip ?? item.label}
      </TooltipContent>
    </Tooltip>
  )
}

// ---------------------------------------------------------------------------
// NavGroup — collapsible group (Relationships) in the icon rail
// ---------------------------------------------------------------------------

function NavGroup({
  item,
  section,
  onNavClick,
  butlerStatusMap,
}: {
  item: NavGroupItem
  section: NavSection
  onNavClick?: () => void
  butlerStatusMap?: ButlerStatusMap
}) {
  const location = useLocation()

  const hasActiveChild = item.children.some((child) =>
    isPathActive(location.pathname, child.path, child.end),
  )

  const [userExpanded, setUserExpanded] = useState(false)
  const expanded = hasActiveChild || userExpanded

  const butlerStatus = item.butler ? butlerStatusMap?.[item.butler] : undefined
  const useButlerMark = section.title === 'Dedicated Butlers' && !!item.butler

  return (
    <div>
      {/* Group header button — icon + chevron */}
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            onClick={() => setUserExpanded((prev) => !prev)}
            aria-expanded={expanded}
            aria-label={item.label}
            className={[
              'relative flex w-full items-center justify-center h-10 border-l-2 transition-colors',
              hasActiveChild
                ? 'border-sidebar-primary bg-sidebar-primary/[0.06] dark:bg-sidebar-primary/[0.06]'
                : 'border-transparent hover:bg-sidebar-accent/50',
            ].join(' ')}
          >
            {/* Glyph */}
            <span className="relative flex size-6 shrink-0 items-center justify-center">
              {useButlerMark ? (
                <ButlerMark name={item.butler!} tone="neutral" />
              ) : (
                <span className="flex size-6 items-center justify-center rounded text-xs font-semibold text-muted-foreground">
                  {item.label[0]}
                </span>
              )}
              <StatusDot status={butlerStatus} />
            </span>
            {/* Chevron at bottom-right */}
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="8"
              height="8"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className={`absolute bottom-1.5 right-1.5 shrink-0 text-muted-foreground/60 transition-transform duration-base ease-out-quart ${expanded ? 'rotate-90' : ''}`}
              aria-hidden="true"
            >
              <path d="m9 18 6-6-6-6" />
            </svg>
          </button>
        </TooltipTrigger>
        <TooltipContent side="right" sideOffset={8}>
          {item.label}
        </TooltipContent>
      </Tooltip>

      {/* Children */}
      <div
        inert={!expanded ? '' : undefined}
        aria-hidden={!expanded}
        className={`overflow-hidden transition-all duration-base ease-out-quart ${
          expanded ? 'max-h-48 opacity-100' : 'max-h-0 opacity-0'
        }`}
      >
        {item.children.map((child) => (
          <Tooltip key={child.path}>
            <TooltipTrigger asChild>
              <NavLink
                to={child.path}
                end={child.end}
                onClick={onNavClick}
                className={({ isActive }) => [
                  railItemClassName(isActive),
                  'pl-2', // indent children
                ].join(' ')}
                aria-label={child.label}
              >
                <span className="flex size-5 items-center justify-center rounded text-[10px] font-semibold text-muted-foreground/70">
                  {child.label[0]}
                </span>
              </NavLink>
            </TooltipTrigger>
            <TooltipContent side="right" sideOffset={8}>
              {child.label}
            </TooltipContent>
          </Tooltip>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// NavSectionGroup — section divider (hidden header in rail mode) + items
// ---------------------------------------------------------------------------

function NavSectionGroup({
  section,
  isFirst,
  onNavClick,
  butlerStatusMap,
  badgeCounts,
}: {
  section: NavSection
  isFirst: boolean
  onNavClick?: () => void
  butlerStatusMap?: ButlerStatusMap
  badgeCounts?: Record<string, number>
}) {
  return (
    <div className={!isFirst ? 'mt-1' : ''}>
      {/* Divider between sections (no visible header in rail mode) */}
      {!isFirst && <div className="mx-2 mb-1 border-t border-border" />}

      {/* Items */}
      <div className="space-y-0.5">
        {section.items.map((item) =>
          isGroup(item) ? (
            <NavGroup
              key={item.label}
              item={item}
              section={section}
              onNavClick={onNavClick}
              butlerStatusMap={butlerStatusMap}
            />
          ) : (
            <FlatNavLink
              key={item.path}
              item={item}
              section={section}
              onNavClick={onNavClick}
              butlerStatusMap={butlerStatusMap}
              badgeCounts={badgeCounts}
            />
          ),
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sidebar footer — status dot summary
// ---------------------------------------------------------------------------

function SidebarFooter({
  butlerStatusMap,
  isLoading,
  isError,
}: {
  butlerStatusMap: ButlerStatusMap
  isLoading: boolean
  isError: boolean
}) {
  const { data: costResponse } = useCostSummary('today')
  const cost = costResponse?.data.total_cost_usd

  if (isLoading) {
    const titleText = 'Loading butlers'
    return (
      <div
        className="flex items-center justify-center border-t border-border p-3"
        title={titleText}
        aria-label={titleText}
      >
        <span className="h-2 w-2 rounded-full bg-muted-foreground/40" aria-hidden="true" />
      </div>
    )
  }

  if (isError) {
    const titleText = 'Butlers query failed'
    return (
      <div
        className="flex items-center justify-center border-t border-border p-3"
        title={titleText}
        aria-label={titleText}
      >
        <span className="h-2 w-2 rounded-full bg-muted-foreground/40" aria-hidden="true" />
      </div>
    )
  }

  const statuses = Object.values(butlerStatusMap)
  const hasError = statuses.some((s) => s === 'error')
  const hasDegraded = statuses.some((s) => s === 'degraded')

  const dotColor = hasError
    ? 'bg-destructive'
    : hasDegraded
      ? 'bg-amber-500'
      : 'bg-green-500'

  const degradedCount = statuses.filter((s) => s === 'degraded').length
  const errorCount = statuses.filter((s) => s === 'error').length
  const parts: string[] = []
  if (errorCount > 0) parts.push(`${errorCount} error${errorCount > 1 ? 's' : ''}`)
  if (degradedCount > 0) parts.push(`${degradedCount} degraded`)
  const costPart = cost != null ? `$${cost.toFixed(2)} today` : ''
  const allParts = [...parts, ...(costPart ? [costPart] : [])]
  const titleText = allParts.length > 0 ? allParts.join(' · ') : 'All systems ok'

  return (
    <div
      className="flex items-center justify-center border-t border-border p-3"
      title={titleText}
      aria-label={titleText}
    >
      <span className={`h-2 w-2 rounded-full ${dotColor}`} aria-hidden="true" />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Sidebar component
// ---------------------------------------------------------------------------

interface SidebarProps {
  /** When true, render expanded labels (used by mobile Sheet). */
  mobileExpanded?: boolean
  onNavClick?: () => void
}

export default function Sidebar({ mobileExpanded = false, onNavClick }: SidebarProps) {
  const { sections: filteredSections, butlerStatusMap, isLoading, isError } = useFilteredNavSections(navSections)
  const badgeCounts = useBadgeCounts()

  // Mobile sheet variant: render with labels (not icon rail)
  if (mobileExpanded) {
    return (
      <MobileSidebar
        sections={filteredSections}
        butlerStatusMap={butlerStatusMap}
        badgeCounts={badgeCounts}
        onNavClick={onNavClick}
      />
    )
  }

  return (
    <TooltipProvider delayDuration={0}>
      <div className="flex h-full flex-col">
        {/* Brand mark */}
        <div
          data-testid="sidebar-brand"
          className="flex h-14 items-center justify-center border-b border-border"
        >
          <span className="text-lg font-semibold" aria-label="Butlers">
            B
          </span>
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto py-2" aria-label="Main navigation">
          {filteredSections.map((section, idx) => (
            <NavSectionGroup
              key={section.title}
              section={section}
              isFirst={idx === 0}
              onNavClick={onNavClick}
              butlerStatusMap={butlerStatusMap}
              badgeCounts={badgeCounts}
            />
          ))}
        </nav>

        {/* Footer */}
        <SidebarFooter butlerStatusMap={butlerStatusMap} isLoading={isLoading} isError={isError} />
      </div>
    </TooltipProvider>
  )
}

// ---------------------------------------------------------------------------
// Mobile sidebar (Sheet context) — renders labels alongside glyphs
// ---------------------------------------------------------------------------

function MobileFlatLink({
  item,
  section,
  onNavClick,
  butlerStatusMap,
  badgeCounts,
}: {
  item: NavFlatItem
  section: NavSection
  onNavClick?: () => void
  butlerStatusMap?: ButlerStatusMap
  badgeCounts?: Record<string, number>
}) {
  const count = item.badgeKey && badgeCounts ? (badgeCounts[item.badgeKey] ?? 0) : 0
  const butlerStatus = item.butler ? butlerStatusMap?.[item.butler] : undefined
  const useButlerMark = section.title === 'Dedicated Butlers' && !!item.butler

  return (
    <NavLink
      to={item.path}
      end={item.end}
      onClick={onNavClick}
      className={({ isActive }) =>
        [
          'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
          isActive
            ? 'bg-sidebar-primary text-sidebar-primary-foreground'
            : 'text-muted-foreground hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground',
        ].join(' ')
      }
      title={item.tooltip ?? item.label}
    >
      <span className="relative flex size-6 shrink-0 items-center justify-center">
        {useButlerMark ? (
          <ButlerMark name={item.butler!} tone="neutral" />
        ) : (
          <span className="flex size-6 items-center justify-center rounded bg-muted text-xs font-semibold">
            {item.label[0]}
          </span>
        )}
        <StatusDot status={butlerStatus} />
      </span>
      <span className="flex-1">{item.label}</span>
      {count > 0 && (
        <span
          className={`ml-auto flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[10px] font-semibold ${
            item.badgeVariant === 'red'
              ? 'bg-red-500 text-white'
              : item.badgeVariant === 'amber'
                ? 'bg-amber-500 text-white'
                : 'bg-primary text-primary-foreground'
          }`}
        >
          {count > 99 ? '99+' : count}
        </span>
      )}
    </NavLink>
  )
}

function MobileNavGroup({
  item,
  section,
  onNavClick,
  butlerStatusMap,
}: {
  item: NavGroupItem
  section: NavSection
  onNavClick?: () => void
  butlerStatusMap?: ButlerStatusMap
}) {
  const location = useLocation()
  const hasActiveChild = item.children.some((child) =>
    isPathActive(location.pathname, child.path, child.end),
  )
  const [userExpanded, setUserExpanded] = useState(false)
  const expanded = hasActiveChild || userExpanded
  const butlerStatus = item.butler ? butlerStatusMap?.[item.butler] : undefined
  const useButlerMark = section.title === 'Dedicated Butlers' && !!item.butler

  return (
    <div>
      <button
        onClick={() => setUserExpanded((prev) => !prev)}
        aria-expanded={expanded}
        className={[
          'flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
          hasActiveChild
            ? 'text-sidebar-accent-foreground'
            : 'text-muted-foreground hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground',
        ].join(' ')}
      >
        <span className="relative flex size-6 shrink-0 items-center justify-center">
          {useButlerMark ? (
            <ButlerMark name={item.butler!} tone="neutral" />
          ) : (
            <span className="flex size-6 items-center justify-center rounded bg-muted text-xs font-semibold">
              {item.label[0]}
            </span>
          )}
          <StatusDot status={butlerStatus} />
        </span>
        <span className="flex-1 text-left">{item.label}</span>
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={`shrink-0 transition-transform duration-base ease-out-quart ${expanded ? 'rotate-90' : ''}`}
          aria-hidden="true"
        >
          <path d="m9 18 6-6-6-6" />
        </svg>
      </button>
      <div
        inert={!expanded ? '' : undefined}
        aria-hidden={!expanded}
        className={`overflow-hidden transition-all duration-base ease-out-quart ${
          expanded ? 'max-h-48 opacity-100' : 'max-h-0 opacity-0'
        }`}
      >
        {item.children.map((child) => (
          <NavLink
            key={child.path}
            to={child.path}
            end={child.end}
            onClick={onNavClick}
            className={({ isActive }) =>
              [
                'flex items-center gap-3 rounded-md pl-9 pr-3 py-2 text-sm font-medium transition-colors',
                isActive
                  ? 'bg-sidebar-primary text-sidebar-primary-foreground'
                  : 'text-muted-foreground hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground',
              ].join(' ')
            }
          >
            <span className="flex size-5 items-center justify-center rounded text-[10px] font-semibold">
              {child.label[0]}
            </span>
            <span className="flex-1">{child.label}</span>
          </NavLink>
        ))}
      </div>
    </div>
  )
}

function MobileSidebar({
  sections,
  butlerStatusMap,
  badgeCounts,
  onNavClick,
}: {
  sections: NavSection[]
  butlerStatusMap: ButlerStatusMap
  badgeCounts: Record<string, number>
  onNavClick?: () => void
}) {
  const { data: costResponse, isLoading } = useCostSummary('today')
  const cost = costResponse?.data.total_cost_usd

  return (
    <div className="flex h-full flex-col">
      <div data-testid="sidebar-brand" className="flex h-14 items-center border-b border-border px-4">
        <span className="text-lg font-semibold">Butlers</span>
      </div>
      <nav className="flex-1 overflow-y-auto p-3">
        {sections.map((section, idx) => (
          <div key={section.title} className={idx > 0 ? 'mt-2' : ''}>
            <h3 className="px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/60">
              {section.title}
            </h3>
            <div className="space-y-1">
              {section.items.map((item) =>
                isGroup(item) ? (
                  <MobileNavGroup
                    key={item.label}
                    item={item}
                    section={section}
                    onNavClick={onNavClick}
                    butlerStatusMap={butlerStatusMap}
                  />
                ) : (
                  <MobileFlatLink
                    key={item.path}
                    item={item}
                    section={section}
                    onNavClick={onNavClick}
                    butlerStatusMap={butlerStatusMap}
                    badgeCounts={badgeCounts}
                  />
                ),
              )}
            </div>
          </div>
        ))}
      </nav>
      <div className="border-t border-border p-4">
        <p className="text-xs text-muted-foreground">Today&apos;s spend</p>
        <p className="text-sm font-medium">
          {isLoading || cost == null ? '--' : `$${cost.toFixed(2)}`}
        </p>
      </div>
    </div>
  )
}
