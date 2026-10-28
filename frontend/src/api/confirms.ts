import { apiFetch } from "./client";
import type { ApiResponse } from "./types";

type ConfirmStatus = "pending" | "answered" | "expired";
type ApplicationStatus = "idle" | "pending" | "applied" | "error";

export interface ConfirmRequest {
  id: string;
  text: string;
  options: { value: string; label: string }[];
  status: ConfirmStatus;
  answer_index: number | null;
  application_status: ApplicationStatus;
  expires_at: string;
  created_at: string;
}

export interface ConfirmPage {
  data: ConfirmRequest[];
  meta: { next_cursor: string | null };
}

type ConfirmAnswerResult =
  | { status: "answered" | "expired"; application_status: ApplicationStatus }
  | { status: "already_handled"; state: ConfirmStatus; application_status: ApplicationStatus }
  | { status: "refused" };

export function getConfirms(before: string | null, signal?: AbortSignal) {
  const query = before === null ? "" : `?${new URLSearchParams({ before })}`;
  return apiFetch<ConfirmPage>(`/confirms${query}`, { signal });
}

export function answerConfirm(id: string, optionIndex: number) {
  return apiFetch<ApiResponse<ConfirmAnswerResult>>(`/confirms/${encodeURIComponent(id)}/answer`, {
    method: "POST",
    body: JSON.stringify({ option_index: optionIndex }),
  });
}
