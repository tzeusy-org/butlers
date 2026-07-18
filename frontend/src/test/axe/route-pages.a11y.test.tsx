// @vitest-environment jsdom
/**
 * Real registered dashboard route accessibility coverage.
 *
 * This suite intentionally mounts production page components with their real
 * data hooks.  The fetch boundary is held pending so each route is inspected
 * in its real loading contract without replacing a page or a domain component
 * with a test double.  Dedicated page suites cover populated/error states
 * where those fixtures already exist; this route sweep ensures every
 * navigation-registered page has an axe pass through its own component tree.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { axe, toHaveNoViolations } from "jest-axe";

import { AppTimezoneProvider } from "@/components/ui/timezone-context";
import { EventBusProvider } from "@/lib/event-bus";
import { ROUTE_AXE_CASES } from "./route-page-cases";

expect.extend(toHaveNoViolations);

async function checkRoute(path: string, Page: React.ComponentType): Promise<void> {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={client}>
      <AppTimezoneProvider timezone="UTC">
        <EventBusProvider>
          <MemoryRouter initialEntries={[path]}>
            <Page />
          </MemoryRouter>
        </EventBusProvider>
      </AppTimezoneProvider>
    </QueryClientProvider>,
  );
  expect(await axe(container, { rules: { "color-contrast": { enabled: false } } })).toHaveNoViolations();
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("a11y (real page): navigation-registered routes", () => {
  for (const [path, Page] of ROUTE_AXE_CASES) {
    it(`${path} has zero axe violations in its real loading state`, async () => {
      vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
      await checkRoute(path, Page);
    });
  }
});
