import { describe, expect, it } from "vitest";

import { POLL_IN_BACKGROUND } from "./poll-policy";
import { queryClient } from "./query-client";

describe("queryClient background polling policy", () => {
  it("defaults interval refetching to visible tabs only", () => {
    expect(queryClient.getDefaultOptions().queries).toEqual(
      expect.objectContaining({ refetchIntervalInBackground: false }),
    );
  });

  it("permits intentional hidden-tab polling only through the named opt-in", () => {
    const options = queryClient.defaultQueryOptions({
      queryKey: ["background-poll-policy-test"],
      queryFn: async () => null,
      refetchIntervalInBackground: POLL_IN_BACKGROUND,
    });

    expect(POLL_IN_BACKGROUND).toBe(true);
    expect(options.refetchIntervalInBackground).toBe(true);
  });
});
