// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";

import { PracticalDrawer } from "@/components/relationship/PracticalDrawer";

const BASE_ENTITY = {
  metadata: {},
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-02-01T00:00:00Z",
};

function render(props: {
  entity: typeof BASE_ENTITY;
  forceOpen: boolean;
  children?: React.ReactNode;
}): string {
  return renderToStaticMarkup(
    <PracticalDrawer {...props}>{props.children ?? null}</PracticalDrawer>,
  );
}

describe("PracticalDrawer", () => {
  it("renders the section toggle button with the title", () => {
    const html = render({ entity: BASE_ENTITY, forceOpen: false });
    expect(html).toContain("Practical details");
  });

  it("shows (action needed) label when forceOpen is true", () => {
    const html = render({ entity: BASE_ENTITY, forceOpen: true });
    expect(html).toContain("action needed");
  });

  it("does not show (action needed) label when forceOpen is false", () => {
    const html = render({ entity: BASE_ENTITY, forceOpen: false });
    expect(html).not.toContain("action needed");
  });

  it("renders children when forceOpen is true (drawer open)", () => {
    const html = render({
      entity: BASE_ENTITY,
      forceOpen: true,
      children: <span>child-content</span>,
    });
    expect(html).toContain("child-content");
  });

  it("does not render children when forceOpen is false (drawer closed)", () => {
    const html = render({
      entity: BASE_ENTITY,
      forceOpen: false,
      children: <span>child-content</span>,
    });
    expect(html).not.toContain("child-content");
  });

  it("renders provenance metadata in the footer when drawer is open", () => {
    const html = render({
      entity: {
        ...BASE_ENTITY,
        metadata: { source_butler: "general" },
      },
      forceOpen: true,
    });
    expect(html).toContain("Source butler");
    expect(html).toContain("general");
  });

  it("renders extra metadata in a details block when present", () => {
    const html = render({
      entity: {
        ...BASE_ENTITY,
        metadata: { custom_key: "custom_value" },
      },
      forceOpen: true,
    });
    expect(html).toContain("Raw metadata");
    expect(html).toContain("custom_value");
  });

  it("does not render a details block when metadata only has display-excluded keys", () => {
    const html = render({
      entity: {
        ...BASE_ENTITY,
        metadata: { source_butler: "general", source_scope: "global", unidentified: false },
      },
      forceOpen: true,
    });
    expect(html).not.toContain("Raw metadata");
  });
});

// ---------------------------------------------------------------------------
// ARIA disclosure pattern (bu-sewk9)
// ---------------------------------------------------------------------------

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

describe("PracticalDrawer ARIA disclosure pattern", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  function renderInDom(props: {
    entity: typeof BASE_ENTITY;
    forceOpen: boolean;
    children?: React.ReactNode;
  }) {
    act(() => {
      root.render(
        <PracticalDrawer entity={props.entity} forceOpen={props.forceOpen}>
          {props.children ?? null}
        </PracticalDrawer>,
      );
    });
  }

  it("toggle button has aria-expanded=false when closed", () => {
    renderInDom({ entity: BASE_ENTITY, forceOpen: false });
    const button = container.querySelector("button");
    expect(button).toBeTruthy();
    expect(button!.getAttribute("aria-expanded")).toBe("false");
  });

  it("toggle button has aria-expanded=true when open", () => {
    renderInDom({ entity: BASE_ENTITY, forceOpen: true });
    const button = container.querySelector("button");
    expect(button).toBeTruthy();
    expect(button!.getAttribute("aria-expanded")).toBe("true");
  });

  it("toggle button aria-expanded changes to true after click", () => {
    renderInDom({ entity: BASE_ENTITY, forceOpen: false });
    const button = container.querySelector("button");
    expect(button!.getAttribute("aria-expanded")).toBe("false");

    act(() => {
      button!.click();
    });

    expect(button!.getAttribute("aria-expanded")).toBe("true");
  });

  it("panel has id matching button aria-controls when open", () => {
    renderInDom({ entity: BASE_ENTITY, forceOpen: true });
    const button = container.querySelector("button");
    const panelId = button!.getAttribute("aria-controls");
    expect(panelId).toBeTruthy();

    const panel = document.getElementById(panelId!);
    expect(panel).toBeTruthy();
  });

  it("panel has role=region when open", () => {
    renderInDom({ entity: BASE_ENTITY, forceOpen: true });
    const button = container.querySelector("button");
    const panelId = button!.getAttribute("aria-controls");
    const panel = document.getElementById(panelId!);
    expect(panel!.getAttribute("role")).toBe("region");
  });

  it("button has aria-controls attribute even when drawer is closed", () => {
    renderInDom({ entity: BASE_ENTITY, forceOpen: false });
    const button = container.querySelector("button");
    // aria-controls should always be present so ATs know which element it controls
    expect(button!.getAttribute("aria-controls")).toBeTruthy();
  });

  it("panel id is stable across re-renders (same component instance)", () => {
    renderInDom({ entity: BASE_ENTITY, forceOpen: true });
    const button = container.querySelector("button");
    const firstPanelId = button!.getAttribute("aria-controls");

    // Re-render with different children — id must not change
    act(() => {
      root.render(
        <PracticalDrawer entity={BASE_ENTITY} forceOpen={true}>
          <span>updated child</span>
        </PracticalDrawer>,
      );
    });

    const secondPanelId = container.querySelector("button")!.getAttribute("aria-controls");
    expect(secondPanelId).toBe(firstPanelId);
  });
});
