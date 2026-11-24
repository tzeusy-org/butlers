import { useState } from "react";
import { useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { answerSecurityQuestion, answerWasRefused, getSecurityQuestion } from "@/api/security-questions.ts";
import type { HoldReason, SecurityQuestion } from "@/api/security-questions.ts";
import { ApiError } from "@/api/client.ts";
import { Button } from "@/components/ui/button";

// ---------------------------------------------------------------------------
// SecurityQuestionPage — route /security/:eventId
//
// "Was this you?" for one authenticated account-security alert. Yes changes nothing. No holds
// the owner channels the owner ticks, through the same protected custody path as a manual hold.
// The page shows masked hints only and posts back the fact ids the server listed; it never
// decides which channel is affected and never answers on a timeout.
// ---------------------------------------------------------------------------

const KIND: Record<string, string> = {
  new_sign_in: "a new sign-in",
  password_changed: "a password change",
  mfa_changed: "a change to two-step verification",
  recovery_changed: "a change to recovery options",
  deletion_scheduled: "a scheduled account deletion",
};

const REASONS: { value: HoldReason; label: string }[] = [
  { value: "disowned_access", label: "Someone else may have access" },
  { value: "stolen", label: "Stolen" },
  { value: "lost", label: "Lost" },
  { value: "replaced", label: "Replaced" },
];

const PRESENTATION: Record<string, string> = {
  pending: "The notification has not been sent yet.",
  presenting: "The notification is being sent.",
  presented: "You were notified.",
  unavailable: "The notification could not be delivered.",
  ambiguous: "The notification may or may not have reached you.",
};

function Open({ question }: { question: SecurityQuestion }) {
  const queryClient = useQueryClient();
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState<HoldReason>("disowned_access");
  // One command id per page view, so a repeated click or a retry is the same command.
  const [commandId] = useState(() => crypto.randomUUID());
  const answer = useMutation({
    mutationFn: (request: Parameters<typeof answerSecurityQuestion>[1]) =>
      answerSecurityQuestion(question.event_id, request),
    gcTime: 0,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["security-question", question.event_id] }),
  });
  const toggle = (id: string) => {
    const next = new Set(ticked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setTicked(next);
  };
  const refused = answer.error && answerWasRefused(answer.error);
  const unknown = answer.error && !refused;
  return (
    <div className="space-y-4">
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">If not, which of your channels may be compromised?</legend>
        {question.candidates.length === 0 && (
          <p className="text-sm text-muted-foreground">No owner channels are on file to hold.</p>
        )}
        {question.candidates.map((candidate) => (
          <label key={candidate.fact_id} className="flex min-h-11 items-center gap-3 text-sm">
            <input
              type="checkbox"
              checked={ticked.has(candidate.fact_id)}
              disabled={candidate.held}
              onChange={() => toggle(candidate.fact_id)}
            />
            <span>
              {candidate.kind.replace("has-", "")} {candidate.hint}
              {candidate.held ? " (already held)" : ""}
            </span>
          </label>
        ))}
        <label className="flex items-center gap-3 text-sm">
          Why
          <select value={reason} onChange={(e) => setReason(e.target.value as HoldReason)} className="min-h-11 rounded border px-2">
            {REASONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
      </fieldset>
      <div className="flex flex-wrap gap-3">
        <Button variant="outline" disabled={answer.isPending} onClick={() => answer.mutate({ answer: "yes" })}>
          Yes, this was me
        </Button>
        <Button
          variant="destructive"
          disabled={answer.isPending || ticked.size === 0}
          onClick={() =>
            answer.mutate({ answer: "no", command_id: commandId, fact_ids: [...ticked], reason })
          }
        >
          No, hold the ticked channels
        </Button>
      </div>
      {refused && (
        <p role="alert" className="text-sm">
          Those channels could not be held. Nothing was changed; check the selection.
        </p>
      )}
      {unknown && (
        <p role="alert" className="text-sm">
          The answer may not have been saved. Reload this page to see whether it was.
        </p>
      )}
    </div>
  );
}

export default function SecurityQuestionPage() {
  const { eventId = "" } = useParams<{ eventId: string }>();
  const query = useQuery({
    queryKey: ["security-question", eventId],
    queryFn: () => getSecurityQuestion(eventId),
    enabled: eventId !== "",
    retry: false,
  });
  if (!eventId) return <p className="text-sm italic text-muted-foreground">Question not found.</p>;
  if (query.isLoading) return <p role="status">Loading question…</p>;
  const question = query.data;
  if (query.isError || !question) {
    const status = query.error instanceof ApiError ? query.error.status : null;
    return (
      <p role="alert" className="text-sm">
        {status === 404 ? "Question not found." : "The question could not be loaded."}
      </p>
    );
  }
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Was this you?</h1>
      <p className="text-sm">
        Your {question.provider} account reported {KIND[question.kind] ?? "a security change"}. The
        sender was verified as {question.provider}; that does not show whether your account was
        compromised.
      </p>
      <p className="text-sm text-muted-foreground">{PRESENTATION[question.presentation]}</p>
      {question.answer === "yes" && <p role="status">You answered yes. Nothing was changed.</p>}
      {question.answer === "no" && (
        <div role="status" className="space-y-1 text-sm">
          <p>You answered no. The chosen channels are held.</p>
          <p>{question.case_recorded ? "A case is open." : "The case is being opened."}</p>
          {question.recovery_door && (
            <p>
              Recover the account at{" "}
              <a href={question.recovery_door} rel="noreferrer noopener" className="underline">
                {question.recovery_door}
              </a>
            </p>
          )}
        </div>
      )}
      {question.answer !== "no" && <Open key={question.answer ?? "open"} question={question} />}
    </div>
  );
}
