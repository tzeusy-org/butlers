import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { answerConfirm, getConfirms, type ConfirmRequest } from "@/api/confirms";
import type { ApiResponse } from "@/api/types";

// No fleet event covers confirmation application; foreground polling is its primary update path.
const CONFIRMS_POLL_MS = 15_000;
const CONFIRMS_KEY = ["confirms"] as const;

export function useConfirms() {
  return useQuery({
    queryKey: CONFIRMS_KEY,
    queryFn: ({ signal }) => getConfirms(signal),
    refetchInterval: CONFIRMS_POLL_MS,
    retry: false,
  });
}

export function useAnswerConfirm(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: async (optionIndex: number) => {
      const { data } = await answerConfirm(id, optionIndex);
      if (data.status === "refused") throw new Error("This question could not accept the answer.");
      return data;
    },
    onSuccess: (result, optionIndex) => {
      // Only the server's confirmed receipt can retire choices before the authoritative refresh.
      queryClient.setQueryData<ApiResponse<ConfirmRequest[]>>(CONFIRMS_KEY, (previous) => {
        if (!previous) return previous;
        return {
          ...previous,
          data: previous.data.map((question) => question.id !== id ? question : {
            ...question,
            status: result.status === "already_handled" ? result.state : result.status,
            answer_index: result.status === "answered" ? optionIndex : null,
            application_status: result.application_status,
          }),
        };
      });
    },
    // A failed response can follow a committed answer. Re-read instead of assuming the write failed.
    onSettled: () => queryClient.invalidateQueries({ queryKey: CONFIRMS_KEY }),
  });
}
