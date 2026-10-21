import { useEffect, useReducer } from "react";
import type { DraftSession } from "@/lib/draft-session";
import { Button } from "./button";
export function DraftNotice({ session }: {
    session: DraftSession | null;
}) {
    const [, redraw] = useReducer((n: number) => n + 1, 0);
    useEffect(() => session?.subscribe(redraw), [session]);
    if (!session)
        return null;
    const state = session.presentation;
    return <div className="space-y-2 text-sm">
    <details className="text-xs text-muted-foreground">
      <summary className="cursor-pointer">Browser draft privacy</summary>
      <p>Drafts stay in this browser profile for up to 24 hours. People using this profile and scripts on this origin can read them. Drafts are not encrypted or synced to the server.</p>
    </details>
    <p role="status" aria-atomic="true" className="text-muted-foreground">
      {state.cleanup ? "Browser draft deletion unconfirmed" : state.waiting ? state.deletion ? "Discarding draft" : "Saving draft" : state.unavailable ? "Draft saving unavailable. Keep this tab open." : state.corrupt ? "The saved draft could not be restored." : state.restored ? "Draft restored" : ""}
    </p>
    {state.restored && !state.closing && <Button type="button" variant="ghost" size="sm" onClick={() => void session.discard()}>Discard draft</Button>}
    {(state.conflict || state.baselineMismatch) && <div role="group" aria-label="Draft conflict" className="space-y-2">
      <p>{state.conflict ? "Draft changed in another tab." : "The source changed since this draft was saved."}</p>
      {state.conflict && <Button type="button" variant="outline" size="sm" onClick={() => void session.chooseThis()}>Use this tab</Button>}{" "}
      <Button type="button" variant="outline" size="sm" onClick={() => void session.chooseOther()}>{state.conflict ? "Load other draft" : "Load draft"}</Button>{" "}
      <Button type="button" variant="ghost" size="sm" onClick={() => void session.discard()}>Discard draft</Button>
    </div>}
    {(state.closing || state.cleanup) && <div role="group" aria-label="Unsaved draft" className="space-y-2">
      <p>{state.unavailable ? state.deletion || state.cleanup ? "A browser draft may remain." : "Latest changes may not be recoverable. A browser draft may still appear." : "Keep this draft before leaving?"}</p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={() => session.keepEditing()}>Keep editing</Button>
        <Button type="button" variant="outline" size="sm" disabled={state.waiting} onClick={() => void session.confirmClose(state.cleanup || state.deletion)}>{state.unavailable ? state.deletion || state.cleanup ? "Retry discard" : "Retry saving" : "Keep draft and close"}</Button>
        {!state.unavailable && <Button type="button" variant="outline" size="sm" disabled={state.waiting} onClick={() => void session.confirmClose(true)}>Discard and close</Button>}
        {state.unavailable && <Button type="button" variant="outline" size="sm" onClick={() => session.closeUnconfirmed()}>{state.deletion || state.cleanup ? "Close with deletion unconfirmed" : "Close with save unconfirmed"}</Button>}
      </div>
    </div>}
  </div>;
}
