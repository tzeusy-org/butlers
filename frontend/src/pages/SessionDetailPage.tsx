import { useEffect } from "react";
import { Link, useParams } from "react-router";

import { Page } from "@/components/ui/page";
import { StatusBadge } from "@/components/sessions/StatusBadge";
import { SessionDossier } from "@/components/sessions/SessionDossier";
import { useGlobalSessionDetail } from "@/hooks/use-sessions";
import { usePageSubject } from "@/lib/page-context.tsx";

// ---------------------------------------------------------------------------
// SessionDetailPage — the one session dossier on the trace spine
// (bu-qvnce.5, pursuit move 5 slice 2).
//
// Always fetches via the global cross-butler endpoint (useGlobalSessionDetail
// -> GET /api/sessions/:id) — there is no more ?butler= dual-fetch path. Any
// inbound link that still carries ?butler= (notification-feed.tsx,
// EventDrawer.tsx, TimelineLedger.tsx, etc.) keeps working: the param is
// simply ignored now, since the global endpoint already returns session.butler
// (SessionDossier links it without needing the query string). 10+ surfaces
// deep-link here (SpendPage, ApprovalsPage, TimelineLedger, notification-feed,
// GlobalActionsRegistrar's post-trigger navigation, et al.) — every inbound
// shape resolves through this one code path.
// ---------------------------------------------------------------------------

export default function SessionDetailPage() {
  const { id = "" } = useParams<{ id: string }>();
  const { data: response, isLoading, isError, error } = useGlobalSessionDetail(id || null);
  const session = response?.data;

  // Page-context enrichment (bu-0ynlk.4): attaches the session id currently
  // in view so a chat message sent from this page (e.g. "why did this fail")
  // arrives grounded without the owner having to repeat the id.
  const setPageSubject = usePageSubject().set;
  useEffect(() => {
    if (!id) return;
    setPageSubject({
      visible_resource: { kind: "session", id },
      visible_summary: `Session ${id.slice(0, 8)}`,
    });
  }, [id, setPageSubject]);

  if (!id) {
    return (
      <Page archetype="detail" title="Session Detail" empty={null}>
        <p className="text-muted-foreground py-12 text-center text-sm">No session ID provided.</p>
      </Page>
    );
  }

  const notFound = !isLoading && !isError && !session;

  return (
    <Page
      archetype="detail"
      title="Session Detail"
      breadcrumbs={[{ label: "Sessions", href: "/sessions" }, { label: id.slice(0, 8) }]}
      status={session ? <StatusBadge success={session.success} error={session.error} /> : undefined}
      loading={isLoading}
      error={isError || notFound ? (error ?? new Error("Session not found")) : null}
      empty={null}
    >
      {session && (
        <>
          <p className="text-xs font-mono text-muted-foreground">{session.id}</p>
          <SessionDossier session={session} />
          <div className="flex items-center gap-4">
            <Link to="/sessions" className="text-xs text-muted-foreground hover:underline">
              &larr; Back to sessions
            </Link>
            {/* Reverse of MessageThread.tsx's forward "View session" link
                (bu-0ynlk.5) -- only rendered once conversation_reply has
                actually stamped a session_id on a dashboard message, and
                only when the owning butler is known to link to. Never
                fabricated: no linked_message means no affordance. */}
            {session.linked_message && session.butler && (
              <Link
                to={`/butlers/${session.butler}`}
                className="text-xs text-muted-foreground hover:underline"
              >
                Asked in chat &rarr;
              </Link>
            )}
          </div>
        </>
      )}
    </Page>
  );
}
