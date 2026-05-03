import { describe, expect, it } from "vitest";
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
