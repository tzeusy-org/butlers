/**
 * Phone entry-route walk — dashboard-design-language §Viewport and Modality
 * Contract (phone band, 44x44 touch floor).
 *
 * Runs only in the `phone` Playwright project (375x667, touch). For every
 * route in the registry it asserts:
 *   - no horizontal page scroll;
 *   - the primary action sits inside the viewport;
 *   - every visible button, link and [role=button] has a >= 44x44 hit area.
 *
 * Two tests per route. The layout test (scroll, clipping) always runs. The
 * touch-target test is `test.fail` while the route carries a `fixme` defect bead:
 * it stays green while the defect exists and goes red once it is fixed, forcing
 * the marker out of the registry. Assertions are never loosened.
 */

import { expect, test, type Page } from "@playwright/test";

import { PHONE_ENTRY_ROUTES, type PhoneEntryRoute } from "./phone-entry-routes";

const MIN_TARGET_PX = 44;

async function openRoute(page: Page, route: PhoneEntryRoute) {
  for (const [glob, body] of Object.entries(route.fixtures)) {
    await page.route(glob, (r) =>
      r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      }),
    );
  }

  await page.goto(route.path);
  await expect(route.primaryAction(page)).toBeVisible();
  await page.waitForLoadState("networkidle");
}

for (const route of PHONE_ENTRY_ROUTES) {
  test(`phone entry route layout: ${route.name}`, async ({ page }) => {
    await openRoute(page, route);

    const overflow = await page.evaluate(() => {
      const el = document.scrollingElement ?? document.documentElement;
      return { scrollWidth: el.scrollWidth, clientWidth: el.clientWidth };
    });
    expect(overflow.scrollWidth, "horizontal page scroll").toBeLessThanOrEqual(
      overflow.clientWidth,
    );

    const viewport = page.viewportSize()!;
    const box = (await route.primaryAction(page).boundingBox())!;
    expect(box.x, "primary action clipped left").toBeGreaterThanOrEqual(0);
    expect(box.y, "primary action clipped top").toBeGreaterThanOrEqual(0);
    expect(box.x + box.width, "primary action clipped right").toBeLessThanOrEqual(viewport.width);
    expect(box.y + box.height, "primary action clipped bottom").toBeLessThanOrEqual(
      viewport.height,
    );
  });

  test(`phone entry route touch targets: ${route.name}`, async ({ page }) => {
    test.fail(!!route.fixme, `touch-target defect tracked in ${route.fixme}`);
    await openRoute(page, route);

    const undersized = await page
      .locator("button, a[href], [role=button]")
      .evaluateAll((els, min) => {
        return els
          .filter((el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return (
              r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"
            );
          })
          .map((el) => {
            const r = el.getBoundingClientRect();
            return {
              el: `${el.tagName.toLowerCase()} "${(el.textContent ?? "").trim().slice(0, 30)}" ${
                el.getAttribute("aria-label") ?? ""
              }`.trim(),
              w: Math.round(r.width),
              h: Math.round(r.height),
            };
          })
          .filter((t) => t.w < min || t.h < min);
      }, MIN_TARGET_PX);
    expect(undersized, `targets under ${MIN_TARGET_PX}x${MIN_TARGET_PX}`).toEqual([]);
  });
}
