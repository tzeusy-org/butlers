import { Link } from "react-router";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { disposeHold, listCustodyHolds } from "@/api/custody.ts";
import type { CustodyHold } from "@/api/custody.ts";
import { Button } from "@/components/ui/button";
import { Time } from "@/components/ui/time";

// ---------------------------------------------------------------------------
// CustodyPage — route /custody
//
// The owner's endpoint holds: what is held (a kind of channel, never an address), since when and
// why, with release and replace. A failed read is "unavailable", never "nothing held": an outage
// must not look like clear. Holding a channel starts from a security question (/security) or the
// host command, where the owner's own channels are listed with masked hints.
// ---------------------------------------------------------------------------

const TARGET: Record<string, string> = {
  "has-email": "An email address",
  "has-handle": "A messaging handle",
  "has-phone": "A phone number",
};

const REASON: Record<string, string> = {
  lost: "Lost",
  stolen: "Stolen",
  replaced: "Replaced",
  disowned_access: "Someone else may have access",
};

function HoldRow({ hold }: { hold: CustodyHold }) {
  const queryClient = useQueryClient();
  // One command id per row and generation, so a repeated click or a retry is the same command.
  const [commandId] = useState(() => crypto.randomUUID());
  const [confirming, setConfirming] = useState<"release" | "replace" | null>(null);
  const dispose = useMutation({
    mutationFn: (action: "release" | "replace") => disposeHold(action, hold, commandId),
    gcTime: 0,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["custody-holds"] }),
  });
  return (
    <li className="space-y-2 rounded border p-3 text-sm">
      <p className="font-medium">{TARGET[hold.target_kind] ?? "A channel"} is held</p>
      <p className="text-muted-foreground">
        {REASON[hold.reason] ?? "Held"} · since <Time value={hold.held_from} mode="absolute" />
      </p>
      <p>
        <Link to={`/custody/${hold.episode_id}`} className="underline underline-offset-2">
          Open the case and recovery checklist
        </Link>
      </p>
      {confirming === null ? (
        <div className="flex flex-wrap gap-3">
          <Button variant="outline" onClick={() => setConfirming("release")}>
            Release
          </Button>
          <Button variant="outline" onClick={() => setConfirming("replace")}>
            Mark replaced
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          <p>
            {confirming === "release"
              ? "Release lets this channel send again. Parked notices flush once."
              : "Mark replaced when the device or account is replaced; this channel stops being held."}
          </p>
          <div className="flex flex-wrap gap-3">
            <Button disabled={dispose.isPending} onClick={() => dispose.mutate(confirming)}>
              Confirm {confirming === "release" ? "release" : "replacement"}
            </Button>
            <Button variant="outline" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {dispose.error && (
        <p role="alert">
          The change may not have been saved. Reload to see whether this channel is still held.
        </p>
      )}
    </li>
  );
}

export default function CustodyPage() {
  const query = useQuery({ queryKey: ["custody-holds"], queryFn: listCustodyHolds, retry: false });
  if (query.isLoading) return <p role="status">Loading holds…</p>;
  if (query.isError || !query.data) {
    return (
      <div className="space-y-2">
        <h1 className="text-lg font-semibold">Endpoint holds</h1>
        <p role="alert" className="text-sm">
          Holds are unavailable. This is not an all-clear; nothing is known to be released.
        </p>
        <Button variant="outline" onClick={() => void query.refetch()}>
          Retry
        </Button>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Endpoint holds</h1>
      {query.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">No channels are held.</p>
      ) : (
        <ul className="space-y-3">
          {query.data.map((hold) => (
            <HoldRow key={`${hold.hold_id}:${hold.generation}`} hold={hold} />
          ))}
        </ul>
      )}
      <p className="text-sm text-muted-foreground">
        To hold a channel, answer <Link to="/security" className="underline">a security question</Link>{" "}
        or use the host command.
      </p>
    </div>
  );
}
