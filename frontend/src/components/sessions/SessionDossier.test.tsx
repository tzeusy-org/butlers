// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react-dom/test-utils";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import { SessionDossier } from "@/components/sessions/SessionDossier";
import type { SessionDetail } from "@/api/types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const BASE_SESSION: SessionDetail = {
  id: "sess-abc123",
  butler: "general",
  prompt: "What is the weather today?",
  trigger_source: "api",
  result: "It is sunny.",
  tool_calls: [],
  duration_ms: 1500,
  trace_id: "trace-001",
  request_id: "req-001",
  cost: null,
  started_at: "2025-03-01T10:00:00Z",
  completed_at: "2025-03-01T10:00:01Z",
  success: true,
  error: null,
  model: "claude-sonnet-4-6",
  input_tokens: 200,
  output_tokens: 50,
  parent_session_id: null,
  complexity: null,
  resolution_source: null,
  process_log: null,
};

describe("SessionDossier", () => {
  let container: HTMLDivElement;
  let root: Root;

  function renderDossier(session: SessionDetail) {
    act(() => {
      root.render(
        <MemoryRouter>
          <SessionDossier session={session} />
        </MemoryRouter>,
      );
    });
  }

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    vi.useFakeTimers();
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("links the Butler row to /butlers/<name> without needing a ?butler= param", () => {
    renderDossier(BASE_SESSION);
    const link = Array.from(document.body.querySelectorAll("a")).find(
      (a) => a.getAttribute("href") === "/butlers/general",
    );
    expect(link).toBeDefined();
  });

  it("links Trace ID to the ingestion timeline pre-filtered by trace", () => {
    renderDossier(BASE_SESSION);
    const link = Array.from(document.body.querySelectorAll("a")).find(
      (a) => a.textContent === "trace-001",
    );
    expect(link?.getAttribute("href")).toBe("/ingestion?trace=trace-001");
  });

  it("links Request ID to /sessions?request=", () => {
    renderDossier(BASE_SESSION);
    const link = Array.from(document.body.querySelectorAll("a")).find(
      (a) => a.textContent === "req-001",
    );
    expect(link?.getAttribute("href")).toBe("/sessions?request=req-001");
  });

  it("links parent_session_id to the parent's own dossier", () => {
    renderDossier({ ...BASE_SESSION, parent_session_id: "sess-parent-1" });
    const link = Array.from(document.body.querySelectorAll("a")).find(
      (a) => a.textContent === "sess-parent-1",
    );
    expect(link?.getAttribute("href")).toBe("/sessions/sess-parent-1");
  });

  it("omits the Request ID / Parent Session sections when absent", () => {
    renderDossier({ ...BASE_SESSION, request_id: null, parent_session_id: null });
    expect(document.body.textContent).not.toContain("Request ID");
    expect(document.body.textContent).not.toContain("Parent Session");
  });

  it("surfaces process_log.exit_code and stderr as root evidence for a failed session", () => {
    renderDossier({
      ...BASE_SESSION,
      success: false,
      error: "Session failed",
      process_log: {
        pid: 123,
        exit_code: 1,
        command: null,
        stderr: "Traceback: boom",
        runtime_type: "claude_code",
        created_at: null,
        expires_at: null,
      },
    });

    expect(document.body.textContent).toContain("Root Evidence");
    expect(document.body.textContent).toContain("Exit Code");
    expect(document.body.textContent).toContain("1");

    // stderr is behind a disclosure toggle, not dumped inline.
    expect(document.body.textContent).not.toContain("Traceback: boom");
    const toggle = Array.from(document.body.querySelectorAll("button")).find(
      (b) => b.textContent === "stderr",
    );
    expect(toggle).toBeDefined();
    act(() => {
      toggle?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(document.body.textContent).toContain("Traceback: boom");
  });

  it("does not show root evidence for a successful session even with a process_log", () => {
    renderDossier({
      ...BASE_SESSION,
      success: true,
      process_log: {
        pid: 123,
        exit_code: 0,
        command: null,
        stderr: null,
        runtime_type: "claude_code",
        created_at: null,
        expires_at: null,
      },
    });
    expect(document.body.textContent).not.toContain("Root Evidence");
  });

  it("shows a live elapsed ticker instead of Duration while the session is running", () => {
    vi.setSystemTime(new Date("2025-03-01T10:00:05Z"));
    renderDossier({
      ...BASE_SESSION,
      success: null,
      completed_at: null,
      started_at: "2025-03-01T10:00:00Z",
    });

    expect(document.body.textContent).toContain("Elapsed");
    expect(document.body.textContent).not.toContain("Duration");
    const elapsedEl = document.body.querySelector('[data-testid="session-elapsed"]');
    expect(elapsedEl?.textContent).toBe("5s");

    act(() => {
      vi.setSystemTime(new Date("2025-03-01T10:00:08Z"));
      vi.advanceTimersByTime(1000);
    });

    expect(document.body.querySelector('[data-testid="session-elapsed"]')?.textContent).toBe("9s");
  });

  it("shows Duration (not Elapsed) once a session is terminal", () => {
    renderDossier({ ...BASE_SESSION, success: true, completed_at: "2025-03-01T10:00:01Z" });
    expect(document.body.textContent).toContain("Duration");
    expect(document.body.textContent).not.toContain("Elapsed");
  });
});
