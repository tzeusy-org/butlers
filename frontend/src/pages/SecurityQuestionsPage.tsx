import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";

import { listSecurityQuestions } from "@/api/security-questions.ts";
import type { SecurityQuestionSummary } from "@/api/security-questions.ts";
import { Time } from "@/components/ui/time";

// ---------------------------------------------------------------------------
// SecurityQuestionsPage — route /security
//
// The unanswered "was this you?" questions, so the owner can find one when its notification never
// arrived or may not have. Unavailable and ambiguous deliveries are listed as such; an unanswered
// question is never shown as calm. A "no" whose case is not yet open stays listed too.
// ---------------------------------------------------------------------------

const KIND: Record<string, string> = {
  new_sign_in: "New sign-in",
  password_changed: "Password change",
  mfa_changed: "Two-step verification change",
  recovery_changed: "Recovery options change",
  deletion_scheduled: "Scheduled account deletion",
};

const DELIVERY: Record<string, string> = {
  pending: "Notification not sent yet",
  presenting: "Notification being sent",
  presented: "You were notified",
  unavailable: "Notification could not be delivered",
  ambiguous: "Notification may not have reached you",
};

function isOpen(question: SecurityQuestionSummary): boolean {
  return question.answer === null || (question.answer === "no" && !question.case_recorded);
}

export default function SecurityQuestionsPage() {
  const query = useQuery({
    queryKey: ["security-questions"],
    queryFn: listSecurityQuestions,
    retry: false,
  });
  if (query.isLoading) return <p role="status">Loading questions…</p>;
  if (query.isError || !query.data) {
    return (
      <p role="alert" className="text-sm">
        The questions could not be loaded. None are being treated as answered.
      </p>
    );
  }
  const open = query.data.filter(isOpen);
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Security questions</h1>
      {open.length === 0 ? (
        <p className="text-sm text-muted-foreground">No unanswered security questions.</p>
      ) : (
        <ul className="space-y-2">
          {open.map((question) => (
            <li key={question.event_id}>
              <Link
                to={`/security/${question.event_id}`}
                className="block min-h-11 rounded border p-3 text-sm underline-offset-2 hover:underline"
              >
                <span className="font-medium">
                  {question.provider} {(KIND[question.kind] ?? "Security change").toLowerCase()}
                </span>
                <span className="block text-muted-foreground">
                  {question.answer === "no" ? "Answered no; the case is being opened" : "Unanswered"}
                  {" · "}
                  {DELIVERY[question.presentation] ?? question.presentation}
                  {" · "}
                  <Time value={question.admitted_at} mode="absolute" />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
