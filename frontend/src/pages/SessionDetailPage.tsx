import { Link, useParams } from "react-router";

import { Page } from "@/components/ui/page";
import { StatusBadge } from "@/components/sessions/StatusBadge";
import { SessionDossier } from "@/components/sessions/SessionDossier";
import { useGlobalSessionDetail } from "@/hooks/use-sessions";

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
          <div>
            <Link to="/sessions" className="text-xs text-muted-foreground hover:underline">
              &larr; Back to sessions
            </Link>
          </div>
        </>
      )}
    </Page>
  );
}
