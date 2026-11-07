import { createContext, useMemo, type ReactNode } from "react";
import type { DraftSession } from "@/lib/draft-session";
class DismissBoundary {
    sessions = new Set<DraftSession>();
    async close(complete: () => void) {
        for (const session of this.sessions)
            if (!await session.requestClose(session.surface === "chat.message"))
                return;
        complete();
    }
}
export const DraftDismissContext = createContext<DismissBoundary | null>(null);
/** Registration changes dismissal only; shared controls never become eligible for persistence. */
export function DraftDismissBoundary({ children }: {
    children: (dismiss: (complete: () => void) => void) => ReactNode;
}) {
    const boundary = useMemo(() => new DismissBoundary(), []);
    return <DraftDismissContext.Provider value={boundary}>{children((complete) => {
            if (![...boundary.sessions].some((session) => session.dirty || session.presentation.cleanup))
                complete();
            else
                void boundary.close(complete);
        })}</DraftDismissContext.Provider>;
}
