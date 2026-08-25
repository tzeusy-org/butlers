import type { SpotifyState } from "@/api/types.ts";
import type { CredentialState } from "./types.ts";

export interface SpotifyProjectionStateInput {
  isLoading: boolean;
  isError: boolean;
  state: SpotifyState | undefined;
}

export function spotifyProjectionState({
  isLoading,
  isError,
  state,
}: SpotifyProjectionStateInput): CredentialState {
  if (isLoading) return "checking";
  if (isError || !state) return "failed";

  switch (state) {
    case "connected":
      return "ok";
    case "unconfigured":
      return "never_set";
    case "authorization_needed":
    case "needs_reauth":
      return "authorization_needed";
    case "error":
      return "failed";
  }
}
