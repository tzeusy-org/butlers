import { apiFetch } from "./client";
import type { components } from "./schema.gen";
import type { ApiResult } from "./request-types";

export type ConfirmRequest = components["schemas"]["ConfirmCardOutput"];
export type ConfirmPage = ApiResult<"/confirms", "GET">;

export function getConfirms(before: string | null, signal?: AbortSignal) {
  return apiFetch("/confirms", {
    query: before === null ? {} : { before: String(before) },
    signal,
  });
}

export function answerConfirm(id: string, optionIndex: number) {
  return apiFetch("/confirms/{confirm_id}/answer", {
    method: "POST",
    path: { confirm_id: String(id) },
    body: { option_index: optionIndex },
  });
}
