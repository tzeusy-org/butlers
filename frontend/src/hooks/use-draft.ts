import { useContext, useEffect, useLayoutEffect, useMemo, useReducer, useRef } from "react";
import { DraftDismissContext } from "@/components/ui/draft-dismiss";
import { DraftSession, type DraftSubmission } from "@/lib/draft-session";
import { activeDraftSessions } from "@/lib/draft-navigation";
import { validDraftFields, type DraftFields, type DraftSurface } from "@/lib/draft-surfaces";
/** Explicit owner registration. Disabled surfaces never read records or write presence metadata. */
export function useDraft<T extends DraftFields>(surface: DraftSurface, context: string[], fields: T, restore: (fields: T) => void, enabled = true, initialFields = fields, sourceRevision?: string | null) {
    const restoreRef = useRef(restore);
    useLayoutEffect(() => { restoreRef.current = restore; });
    enabled = enabled && validDraftFields(surface, fields) && context.every((part) => typeof part === "string" && part.length > 0);
    const identity = JSON.stringify(context);
    const session = useMemo(() => enabled ? new DraftSession(surface, JSON.parse(identity) as string[], fields, (next) => restoreRef.current(next as T), initialFields, sourceRevision) : null,
    // Initial fields belong to this exact task identity. Edits do not replace the instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [surface, identity, enabled]);
    const boundary = useContext(DraftDismissContext);
    const [, redraw] = useReducer((n: number) => n + 1, 0);
    useLayoutEffect(() => {
        if (!session)
            return;
        const unsubscribe = session.subscribe(redraw);
        session.start();
        boundary?.sessions.add(session);
        activeDraftSessions.add(session);
        window.addEventListener("focus", session.focus);
        document.addEventListener("visibilitychange", session.focus);
        return () => {
            unsubscribe();
            boundary?.sessions.delete(session);
            activeDraftSessions.delete(session);
            window.removeEventListener("focus", session.focus);
            document.removeEventListener("visibilitychange", session.focus);
            session.dispose();
        };
    }, [session, boundary]);
    useEffect(() => { session?.edit(fields); });
    return {
        session,
        current: (submission: DraftSubmission | null) => !session || (!!submission && session.current(submission)),
        capture: (): DraftSubmission | null => session?.capture(fields) ?? null,
        flush: () => session?.flush(),
        prepare: () => session?.prepare() ?? true,
        finish: () => session?.finish(),
        submitted: async (submission: DraftSubmission | null, complete: () => void) => {
            if (!session || !submission)
                complete();
            else
                await session.submitted(submission, complete);
        },
        dismiss: (complete: () => void) => { if (!session || (!session.dirty && !session.presentation.cleanup))
            complete();
        else
            void session.requestClose(surface === "chat.message").then((allowed) => { if (allowed)
                complete(); }); },
    };
}
