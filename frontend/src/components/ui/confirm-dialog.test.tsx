// @vitest-environment jsdom
/**
 * ConfirmDialog — shared consequential-action confirm (bu-ep4ks.11).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

import { ConfirmDialog } from "./confirm-dialog";

describe("ConfirmDialog", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    document.body.innerHTML = "";
  });

  it("renders nothing interactive-visible while closed", () => {
    act(() => {
      root.render(
        <ConfirmDialog
          open={false}
          onOpenChange={() => {}}
          title="Delete everything?"
          onConfirm={() => {}}
          testId="test-confirm"
        />,
      );
    });
    expect(document.querySelector('[data-testid="test-confirm"]')).toBeNull();
  });

  it("shows title, description, and calls onConfirm when the confirm action is clicked", () => {
    const onConfirm = vi.fn();
    act(() => {
      root.render(
        <ConfirmDialog
          open
          onOpenChange={() => {}}
          title="Acknowledge all failed notifications?"
          description="This clears every failed notification at once."
          onConfirm={onConfirm}
          testId="test-confirm"
        />,
      );
    });

    expect(document.querySelector('[data-testid="test-confirm"]')?.textContent).toContain(
      "Acknowledge all failed notifications?",
    );
    expect(document.body.textContent).toContain(
      "This clears every failed notification at once.",
    );

    const confirmBtn = document.querySelector<HTMLButtonElement>(
      '[data-testid="test-confirm-confirm"]',
    );
    expect(confirmBtn).not.toBeNull();
    act(() => confirmBtn?.click());
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("disables both cancel and confirm while pending, and swaps in the pending label", () => {
    act(() => {
      root.render(
        <ConfirmDialog
          open
          onOpenChange={() => {}}
          title="Trigger an immediate QA patrol cycle now?"
          confirmLabel="Force patrol"
          pendingLabel="Patrolling…"
          pending
          onConfirm={() => {}}
          testId="test-confirm"
        />,
      );
    });

    const confirmBtn = document.querySelector<HTMLButtonElement>(
      '[data-testid="test-confirm-confirm"]',
    );
    expect(confirmBtn?.textContent).toBe("Patrolling…");
    expect(confirmBtn?.disabled).toBe(true);

    const cancelBtn = Array.from(document.querySelectorAll("button")).find(
      (b) => b.textContent === "Cancel",
    );
    expect(cancelBtn?.disabled).toBe(true);
  });

  it("renders evidence children between the header and the footer", () => {
    act(() => {
      root.render(
        <ConfirmDialog
          open
          onOpenChange={() => {}}
          title="Reset?"
          onConfirm={() => {}}
          testId="test-confirm"
        >
          <p data-testid="evidence-row">evidence line</p>
        </ConfirmDialog>,
      );
    });
    expect(document.querySelector('[data-testid="evidence-row"]')?.textContent).toBe(
      "evidence line",
    );
  });

  it("a second click while pending does not fire onConfirm again (no double-fire)", () => {
    const onConfirm = vi.fn();
    act(() => {
      root.render(
        <ConfirmDialog
          open
          onOpenChange={() => {}}
          title="Confirm?"
          pending
          onConfirm={onConfirm}
          testId="test-confirm"
        />,
      );
    });
    const confirmBtn = document.querySelector<HTMLButtonElement>(
      '[data-testid="test-confirm-confirm"]',
    );
    act(() => confirmBtn?.click());
    act(() => confirmBtn?.click());
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
