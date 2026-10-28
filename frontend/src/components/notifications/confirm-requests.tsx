import { useRef, useState } from "react";

import { ApiError } from "@/api/client";
import type { ConfirmRequest } from "@/api/confirms";
import { Button } from "@/components/ui/button";
import { Query } from "@/components/ui/query-boundary";
import { Section, SectionContent } from "@/components/ui/Section";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { useAnswerConfirm, useConfirms } from "@/hooks/use-confirms";
import { useTickingNow } from "@/hooks/use-ticking-now";

const EXPIRY_TICK_MS = 1_000;

function ConfirmQuestion({ question, now }: { question: ConfirmRequest; now: number }) {
  const answer = useAnswerConfirm(question.id);
  const submittingRef = useRef(false);
  const [submitting, setSubmitting] = useState(false);
  const expired = question.status === "expired" ||
    (question.status === "pending" && Date.parse(question.expires_at) <= now);
  const canAnswer = question.status === "pending" && !expired && !submitting && !answer.isPending;
  function submit(index: number) {
    if (!canAnswer || submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    answer.mutate(index, { onSettled: () => {
      submittingRef.current = false;
      setSubmitting(false);
    } });
  }
  const selected = question.answer_index === null ? undefined : question.options[question.answer_index];
  let receipt: string | undefined;
  if (expired) receipt = "This question has expired.";
  else if (question.status === "answered") {
    receipt = question.application_status === "applied" ? "Answer applied."
      : question.application_status === "error" ? "Answer recorded. Applying it failed; it will be retried."
      : "Answer recorded. Waiting for it to be applied.";
  } else if (submitting || answer.isPending) receipt = "Recording your answer…";

  return (
    <li className="border-b border-border/60 py-4 first:pt-0 last:border-0 last:pb-0">
      <fieldset className="min-w-0 space-y-3" disabled={!canAnswer}>
        <legend className="whitespace-pre-wrap break-words text-sm leading-relaxed">{question.text}</legend>
        <div className="flex flex-wrap gap-2">
          {question.options.map((option, index) => (
            <Button key={option.value} type="button" variant="outline" size="sm"
              className="h-auto min-h-8 whitespace-normal text-left [@media(pointer:coarse)]:min-h-11"
              disabled={!canAnswer} aria-pressed={question.answer_index === index}
              onClick={() => submit(index)}>
              {option.label}
            </Button>
          ))}
        </div>
      </fieldset>
      <div className="mt-3 space-y-1 text-xs text-muted-foreground" aria-live="polite">
        {selected && <p>Your answer: {selected.label}</p>}
        {receipt && <p>{receipt}</p>}
        {question.status === "pending" && !expired && (
          <p>Expires <Time value={question.expires_at} mode="absolute" /></p>
        )}
      </div>
      {answer.isError && question.status === "pending" && !expired && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          Could not confirm whether your answer was recorded. Refresh or try again.
        </p>
      )}
    </li>
  );
}

/** Informational choices share the signed-confirm record with Telegram. */
export function ConfirmRequests() {
  const questions = useConfirms();
  const now = useTickingNow(EXPIRY_TICK_MS);
  // The approved transport stays dark until trusted runtime composition is installed.
  if (questions.error instanceof ApiError && questions.error.status === 503 &&
    (questions.error.code === "confirm_runtime_unavailable" || questions.error.message === "confirm_runtime_unavailable")) {
    return null;
  }
  if (questions.status === "success" && questions.data.pages.every((page) => page.data.length === 0)) return null;

  return (
    <Section eyebrow="Questions for you" className="border-b border-border/60 pb-5">
      <SectionContent>
        <Query q={questions}
          loadingFallback={<Skeleton role="status" className="h-20 w-full" aria-label="Loading questions" />}
          errorFallback={
            <div role="alert" className="space-y-2 text-sm">
              <p className="text-destructive">Questions could not be loaded.</p>
              <Button variant="outline" size="sm" disabled={questions.isFetching}
                onClick={() => { void questions.refetch(); }}>Retry</Button>
            </div>
          }>
          {({ pages }) => (
            <>
              <ul>{pages.flatMap((page) => page.data).map((question) => <ConfirmQuestion key={question.id} question={question} now={now} />)}</ul>
              {questions.hasNextPage && (
                <Button variant="outline" size="sm" className="mt-4"
                  disabled={questions.isFetching}
                  onClick={() => { void questions.fetchNextPage({ cancelRefetch: false }); }}>
                  {questions.isFetchingNextPage ? "Loading more…" : "Load more"}
                </Button>
              )}
            </>
          )}
        </Query>
      </SectionContent>
    </Section>
  );
}

