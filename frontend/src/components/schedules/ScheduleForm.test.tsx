// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import { ScheduleForm } from "@/components/schedules/ScheduleForm";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function flush(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

function setInputValue(
  element: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement,
  value: string,
) {
  const prototype = Object.getPrototypeOf(element);
  const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
  descriptor?.set?.call(element, value);
  const eventType = element instanceof HTMLSelectElement ? "change" : "input";
  element.dispatchEvent(new Event(eventType, { bubbles: true }));
}

function findButton(label: string): HTMLButtonElement | undefined {
  return Array.from(document.body.querySelectorAll("button")).find((button) =>
    button.textContent?.includes(label),
  );
}

describe("ScheduleForm dual-mode behavior", () => {
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
    vi.restoreAllMocks();
  });

  it("submits prompt-mode payload with prompt field", async () => {
    const onSubmit = vi.fn();

    await act(async () => {
      root.render(
        <ScheduleForm open onOpenChange={() => {}} onSubmit={onSubmit} />,
      );
      await flush();
    });

    const nameInput = document.body.querySelector("#schedule-name");
    const cronInput = document.body.querySelector("#schedule-cron");
    const promptInput = document.body.querySelector("#schedule-prompt");
    expect(nameInput).toBeInstanceOf(HTMLInputElement);
    expect(cronInput).toBeInstanceOf(HTMLInputElement);
    expect(promptInput).toBeInstanceOf(HTMLTextAreaElement);

    await act(async () => {
      setInputValue(nameInput as HTMLInputElement, "daily-review");
      setInputValue(cronInput as HTMLInputElement, "0 9 * * *");
      setInputValue(promptInput as HTMLTextAreaElement, "Run daily review");
      await flush();
    });

    const submitButton = findButton("Create Schedule");
    expect(submitButton).toBeDefined();

    await act(async () => {
      submitButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith({
      name: "daily-review",
      cron: "0 9 * * *",
      dispatch_mode: "prompt",
      prompt: "Run daily review",
    });
  });

  it("submits job-mode payload with parsed job args", async () => {
    const onSubmit = vi.fn();

    await act(async () => {
      root.render(
        <ScheduleForm open onOpenChange={() => {}} onSubmit={onSubmit} />,
      );
      await flush();
    });

    const modeSelect = document.body.querySelector("#schedule-dispatch-mode");
    const nameInput = document.body.querySelector("#schedule-name");
    const cronInput = document.body.querySelector("#schedule-cron");
    expect(modeSelect).toBeInstanceOf(HTMLSelectElement);
    expect(nameInput).toBeInstanceOf(HTMLInputElement);
    expect(cronInput).toBeInstanceOf(HTMLInputElement);

    await act(async () => {
      setInputValue(modeSelect as HTMLSelectElement, "job");
      await flush();
    });

    const jobNameInput = document.body.querySelector("#schedule-job-name");
    const jobArgsInput = document.body.querySelector("#schedule-job-args");
    expect(jobNameInput).toBeInstanceOf(HTMLInputElement);
    expect(jobArgsInput).toBeInstanceOf(HTMLTextAreaElement);

    await act(async () => {
      setInputValue(nameInput as HTMLInputElement, "eligibility-sweep");
      setInputValue(cronInput as HTMLInputElement, "*/5 * * * *");
      setInputValue(jobNameInput as HTMLInputElement, "switchboard.eligibility_sweep");
      setInputValue(jobArgsInput as HTMLTextAreaElement, '{"policy_tier":"default"}');
      await flush();
    });

    const submitButton = findButton("Create Schedule");
    expect(submitButton).toBeDefined();

    await act(async () => {
      submitButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith({
      name: "eligibility-sweep",
      cron: "*/5 * * * *",
      dispatch_mode: "job",
      job_name: "switchboard.eligibility_sweep",
      job_args: { policy_tier: "default" },
    });
  });

  it("blocks submission when job args JSON is invalid", async () => {
    const onSubmit = vi.fn();

    await act(async () => {
      root.render(
        <ScheduleForm open onOpenChange={() => {}} onSubmit={onSubmit} />,
      );
      await flush();
    });

    const modeSelect = document.body.querySelector("#schedule-dispatch-mode");
    const nameInput = document.body.querySelector("#schedule-name");
    const cronInput = document.body.querySelector("#schedule-cron");
    expect(modeSelect).toBeInstanceOf(HTMLSelectElement);

    await act(async () => {
      setInputValue(modeSelect as HTMLSelectElement, "job");
      await flush();
    });

    const jobNameInput = document.body.querySelector("#schedule-job-name");
    const jobArgsInput = document.body.querySelector("#schedule-job-args");
    expect(jobNameInput).toBeInstanceOf(HTMLInputElement);
    expect(jobArgsInput).toBeInstanceOf(HTMLTextAreaElement);

    await act(async () => {
      setInputValue(nameInput as HTMLInputElement, "bad-job");
      setInputValue(cronInput as HTMLInputElement, "0 * * * *");
      setInputValue(jobNameInput as HTMLInputElement, "switchboard.bad_job");
      setInputValue(jobArgsInput as HTMLTextAreaElement, "{invalid}");
      await flush();
    });

    const submitButton = findButton("Create Schedule");
    expect(submitButton).toBeDefined();

    await act(async () => {
      submitButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(onSubmit).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("Job args must be valid JSON");
  });
});
