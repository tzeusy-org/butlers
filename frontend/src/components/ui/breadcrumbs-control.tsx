import { createContext, useContext, useState, type ReactNode } from "react";

// ---------------------------------------------------------------------------
// BreadcrumbsControlContext
//
// Bridges the gap between <Page> (inside <main>) and <PageHeader> (in the
// shell header). When a page supplies explicit breadcrumbs via the <Page>
// `breadcrumbs` prop, it calls setSupplyingBreadcrumbs(true) so that
// PageHeader can suppress its URL-segment auto-builder.
//
// Usage:
//   - Wrap the app in <BreadcrumbsControlProvider> (done in RootLayout).
//   - <Page> calls setSupplyingBreadcrumbs(true/false) via useEffect.
//   - <PageHeader> reads isSupplyingBreadcrumbs and sets hideBreadcrumbs.
// ---------------------------------------------------------------------------

interface BreadcrumbsControlContextValue {
  isSupplyingBreadcrumbs: boolean;
  setSupplyingBreadcrumbs: (value: boolean) => void;
}

const BreadcrumbsControlContext = createContext<BreadcrumbsControlContextValue>({
  isSupplyingBreadcrumbs: false,
  setSupplyingBreadcrumbs: () => undefined,
});

export function BreadcrumbsControlProvider({ children }: { children: ReactNode }) {
  const [isSupplyingBreadcrumbs, setSupplyingBreadcrumbs] = useState(false);

  return (
    <BreadcrumbsControlContext.Provider value={{ isSupplyingBreadcrumbs, setSupplyingBreadcrumbs }}>
      {children}
    </BreadcrumbsControlContext.Provider>
  );
}

export function useBreadcrumbsControl(): BreadcrumbsControlContextValue {
  return useContext(BreadcrumbsControlContext);
}
