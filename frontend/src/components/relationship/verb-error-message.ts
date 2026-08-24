/**
 * verbErrorMessage: turn a failed entity-verb write into one honest sentence.
 *
 * Lives in its own module rather than beside EntityVerbRail because
 * react-refresh/only-export-components rejects a non-component export from a
 * component file: Fast Refresh cannot hot-swap a module that also exports a
 * plain function.
 */

import { ApiError } from "@/api/client";

/**
 * Turn a failed verb write into one honest sentence.
 *
 * The backend answers these routes with a structured `detail` object rather
 * than a bare string, so `ApiError.message` is a JSON blob unless we read the
 * dict ourselves. Three cases are worth naming explicitly:
 *   409 -> the record already exists (dedupe, not failure);
 *   403 -> the owner gate rejected the caller;
 *   422 -> the tool rejected the input (bad direction, reserved type).
 */
export function verbErrorMessage(error: unknown, alreadyExists: string): string {
  if (!(error instanceof ApiError)) {
    return error instanceof Error && error.message
      ? error.message
      : "Something went wrong. Nothing was recorded.";
  }
  const detail =
    error.detail && typeof error.detail === "object"
      ? (error.detail as Record<string, unknown>)
      : undefined;
  const detailMessage = typeof detail?.message === "string" ? detail.message : undefined;

  if (error.status === 409) return alreadyExists;
  if (error.status === 403) {
    return "Only the owner can write to this record.";
  }
  if (error.status === 404) {
    return "This entity no longer exists.";
  }
  return detailMessage || error.message || "Nothing was recorded.";
}
