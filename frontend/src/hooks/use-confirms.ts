import { useInfiniteQuery, useMutation, useQueryClient, type InfiniteData } from "@tanstack/react-query";

import { answerConfirm, getConfirms, type ConfirmPage } from "@/api/confirms";

// No fleet event covers confirmation application; foreground polling is its primary update path.
const CONFIRMS_POLL_MS = 15_000;
const CONFIRMS_KEY = ["confirms"] as const;

export function useConfirms() {
  return useInfiniteQuery({
    queryKey: CONFIRMS_KEY,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => getConfirms(pageParam, signal),
    getNextPageParam: (page) => page.meta.next_cursor,
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
      queryClient.setQueryData<InfiniteData<ConfirmPage, string | null>>(CONFIRMS_KEY, (previous) => {
        if (!previous) return previous;
        return {
          ...previous,
          pages: previous.pages.map((page) => ({
            ...page,
            data: page.data.map((question) => question.id !== id ? question : {
              ...question,
              status: result.status === "already_handled" ? result.state : result.status,
              answer_index: result.status === "answered" ? optionIndex : null,
              application_status: result.application_status,
            }),
          })),
        };
      });
    },
    // A failed response can follow a committed answer. Re-read instead of assuming the write failed.
    onSettled: () => queryClient.invalidateQueries({ queryKey: CONFIRMS_KEY }),
  });
}
