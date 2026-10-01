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
 * A route that fails is `fixme` in the registry with its defect bead; the
 * assertions are never loosened.
 */

import { expect, test } from "@playwright/test";

import { PHONE_ENTRY_ROUTES } from "./phone-entry-routes";

const MIN_TARGET_PX = 44;

for (const route of PHONE_ENTRY_ROUTES) {
  test(`phone entry route: ${route.name}`, async ({ page }) => {
    test.fixme(!!route.fixme, `layout defect tracked in ${route.fixme}`);

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
    const action = route.primaryAction(page);
    await expect(action).toBeVisible();
    await page.waitForLoadState("networkidle");

    const overflow = await page.evaluate(() => {
      const el = document.scrollingElement ?? document.documentElement;
      return { scrollWidth: el.scrollWidth, clientWidth: el.clientWidth };
    });
    expect(overflow.scrollWidth, "horizontal page scroll").toBeLessThanOrEqual(
      overflow.clientWidth,
    );

    const viewport = page.viewportSize()!;
    const box = (await action.boundingBox())!;
    expect(box.x, "primary action clipped left").toBeGreaterThanOrEqual(0);
    expect(box.y, "primary action clipped top").toBeGreaterThanOrEqual(0);
    expect(box.x + box.width, "primary action clipped right").toBeLessThanOrEqual(viewport.width);
    expect(box.y + box.height, "primary action clipped bottom").toBeLessThanOrEqual(
      viewport.height,
    );

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
