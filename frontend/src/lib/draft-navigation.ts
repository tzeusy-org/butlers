import type { createBrowserRouter } from "react-router";
import type { DraftSession } from "./draft-session";
export const activeDraftSessions = new Set<DraftSession>();
/** Installed on the real data router, not on isolated view/test renderers. No unload prompt. */
export function installDraftNavigationGuard(router: ReturnType<typeof createBrowserRouter>) {
    const key = "unsent-drafts";
    const shouldBlock = () => [...activeDraftSessions].some((session) => session.dirty || session.presentation.cleanup);
    router.getBlocker(key, shouldBlock);
    let deciding = false;
    router.subscribe(() => {
        const blocker = router.getBlocker(key, shouldBlock);
        if (deciding || blocker.state !== "blocked")
            return;
        deciding = true;
        void (async () => {
            try {
                for (const session of activeDraftSessions) {
                    if (!await session.requestClose(session.surface === "chat.message")) {
                        blocker.reset?.();
                        return;
                    }
                }
                blocker.proceed?.();
            }
            finally {
                deciding = false;
            }
        })();
    });
}
