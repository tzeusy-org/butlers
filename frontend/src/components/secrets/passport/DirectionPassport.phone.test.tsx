// @vitest-environment jsdom
/**
 * Phone layout for the deep-linked Passport page (bu-h40h2b.18).
 *
 * The book body used to hard-code a "296px 1fr" grid, so a Telegram deep link on a phone got a
 * 296px spine and a sliver of page. The spec's Viewport and Modality Contract makes <768px the
 * phone band: one column there, two from md up, with a deep-linked page leading and focused.
 */

import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { DirectionPassport } from "@/components/secrets/passport";
import { MOCK_INVENTORY } from "@/components/secrets/passport/mock-data";

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

const mounted: Array<{ container: HTMLDivElement; root: Root }> = [];

afterEach(() => {
  while (mounted.length > 0) {
    const entry = mounted.pop()!;
    act(() => entry.root.unmount());
    entry.container.remove();
  }
});

async function mountAt(path: string): Promise<HTMLDivElement> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push({ container, root });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[path]}>
          <DirectionPassport inventory={MOCK_INVENTORY} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  return container;
}

describe("DirectionPassport at phone width", () => {
  it("stacks to one column below md and splits into spine and page from md up", async () => {
    const container = await mountAt("/secrets");
    const body = container.querySelector<HTMLElement>('[data-testid="passport-book-body"]')!;

    expect(body.className).toContain("grid-cols-1");
    expect(body.className).toContain("md:grid-cols-[296px_1fr]");
    expect(body.style.gridTemplateColumns).toBe("");
  });

  it("caps the stacked spine so the page is not pushed a full screen away", async () => {
    const container = await mountAt("/secrets");
    const body = container.querySelector<HTMLElement>('[data-testid="passport-book-body"]')!;

    expect(body.className).toContain("max-md:[&>nav]:max-h-72");
  });

  it("leads with the deep-linked page and moves focus onto it", async () => {
    const container = await mountAt("/secrets?focus=u:google");
    const page = container.querySelector<HTMLElement>('[data-testid="passport-page"]')!;

    expect(page.className).toContain("max-md:order-first");
    expect(document.activeElement).toBe(page);
  });

  it("keeps the spine first and focus untouched when there is no deep link", async () => {
    const container = await mountAt("/secrets");
    const page = container.querySelector<HTMLElement>('[data-testid="passport-page"]')!;

    expect(page.className).not.toContain("max-md:order-first");
    expect(document.activeElement).not.toBe(page);
  });
});
