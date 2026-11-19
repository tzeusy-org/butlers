import { Link } from "react-router";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  disposeHold,
  holdChannels,
  listCustodyHolds,
  listCustodyTargets,
} from "@/api/custody.ts";
import type { CustodyHold, HoldReason } from "@/api/custody.ts";
import { ApiError } from "@/api/client.ts";
import { Button } from "@/components/ui/button";
import { Time } from "@/components/ui/time";

// ---------------------------------------------------------------------------
// CustodyPage — route /custody
//
// The owner's endpoint holds: what is held (a kind of channel, never an address), since when and
// why, with release and replace, and a "Hold a channel" action over the owner's own channels
// (masked hints; the page posts back only the fact ids the server listed). A failed read is
// "unavailable", never "nothing held": an outage must not look like clear.
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
  const [confirming, setConfirming] = useState<"release" | "replace" | null>(
    null,
  );
  const dispose = useMutation({
    mutationFn: (action: "release" | "replace") =>
      disposeHold(action, hold, commandId),
    gcTime: 0,
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: ["custody-holds"] }),
  });
  return (
    <li className="space-y-2 rounded border p-3 text-sm">
      <p className="font-medium">
        {TARGET[hold.target_kind] ?? "A channel"} is held
      </p>
      <p className="text-muted-foreground">
        {REASON[hold.reason] ?? "Held"} · since{" "}
        <Time value={hold.held_from} mode="absolute" />
      </p>
      <p>
        <Link
          to={`/custody/${hold.episode_id}`}
          className="underline underline-offset-2"
        >
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
            <Button
              disabled={dispose.isPending}
              onClick={() => dispose.mutate(confirming)}
            >
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
          The change may not have been saved. Reload to see whether this channel
          is still held.
        </p>
      )}
    </li>
  );
}

const REASONS: { value: HoldReason; label: string }[] = [
  { value: "disowned_access", label: "Someone else may have access" },
  { value: "stolen", label: "Stolen" },
  { value: "lost", label: "Lost" },
  { value: "replaced", label: "Replaced" },
];

const KIND_LABEL: Record<string, string> = {
  "has-email": "Email",
  "has-handle": "Messaging handle",
  "has-phone": "Phone",
};

function HoldAChannel() {
  const queryClient = useQueryClient();
  const targets = useQuery({
    queryKey: ["custody-targets"],
    queryFn: listCustodyTargets,
    retry: false,
  });
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState<HoldReason>("lost");
  const [confirming, setConfirming] = useState(false);
  // A command id names one selection. It is kept only for an identical retry after an unknown
  // outcome; a changed selection or reason, or any definite outcome, mints a new one.
  const command = useRef<{ id: string; signature: string } | null>(null);
  const hold = useMutation({
    mutationFn: () => {
      const signature = `${reason}:${[...ticked].sort().join(",")}`;
      if (command.current?.signature !== signature) {
        command.current = { id: crypto.randomUUID(), signature };
      }
      return holdChannels({
        command_id: command.current.id,
        fact_ids: [...ticked],
        reason,
      });
    },
    gcTime: 0,
    onSettled: (_data, error) => {
      const definite =
        error === null || (error instanceof ApiError && error.status === 409);
      if (definite) {
        command.current = null;
        setTicked(new Set());
        setConfirming(false);
      }
      void queryClient.invalidateQueries({ queryKey: ["custody-holds"] });
      void queryClient.invalidateQueries({ queryKey: ["custody-targets"] });
    },
  });
  const refused = hold.error instanceof ApiError && hold.error.status === 409;
  const conflict =
    refused &&
    (hold.error.detail as { reason?: string } | undefined)?.reason ===
      "command_conflict";
  const unknown = hold.error !== null && !refused;
  const toggle = (id: string) => {
    const next = new Set(ticked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setTicked(next);
    setConfirming(false);
  };
  if (targets.isLoading) return <p role="status">Loading your channels…</p>;
  if (targets.isError || !targets.data) {
    return (
      <section className="space-y-2" aria-labelledby="hold-heading">
        <h2 id="hold-heading" className="font-medium">
          Hold a channel
        </h2>
        <p role="alert" className="text-sm">
          Your channels could not be loaded, so nothing can be held from here.
          Nothing was changed.
        </p>
        <Button variant="outline" onClick={() => void targets.refetch()}>
          Retry
        </Button>
      </section>
    );
  }
  return (
    <section className="space-y-3" aria-labelledby="hold-heading">
      <h2 id="hold-heading" className="font-medium">
        Hold a channel
      </h2>
      <p className="text-sm text-muted-foreground">
        A held channel stops sending until you release it. Pick only the
        channels that may be compromised.
      </p>
      {targets.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No owner channels are on file to hold.
        </p>
      ) : (
        <fieldset className="space-y-2">
          <legend className="sr-only">Channels to hold</legend>
          {targets.data.map((target) => (
            <label
              key={target.fact_id}
              className="flex min-h-11 items-center gap-3 text-sm"
            >
              <input
                type="checkbox"
                checked={ticked.has(target.fact_id)}
                disabled={target.held}
                onChange={() => toggle(target.fact_id)}
              />
              <span>
                {KIND_LABEL[target.kind] ?? "Channel"} {target.hint}
                {target.held ? " (already held)" : ""}
              </span>
            </label>
          ))}
          <label className="flex items-center gap-3 text-sm">
            Why
            <select
              value={reason}
              onChange={(event) => setReason(event.target.value as HoldReason)}
              className="min-h-11 rounded border px-2"
            >
              {REASONS.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
        </fieldset>
      )}
      {!confirming ? (
        <Button
          variant="destructive"
          disabled={ticked.size === 0}
          onClick={() => setConfirming(true)}
        >
          Hold the ticked channels
        </Button>
      ) : (
        <div className="space-y-2">
          <p className="text-sm">
            Hold {ticked.size} channel{ticked.size === 1 ? "" : "s"}? Sends
            through them stop until you release.
          </p>
          <div className="flex flex-wrap gap-3">
            <Button
              variant="destructive"
              disabled={hold.isPending}
              onClick={() => hold.mutate()}
            >
              Confirm hold
            </Button>
            <Button variant="outline" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {hold.isSuccess && (
        <p role="status" className="text-sm">
          The ticked channels are held.
        </p>
      )}
      {conflict && (
        <p role="alert" className="text-sm">
          That request clashed with an earlier one. Nothing was changed; pick
          the channels again.
        </p>
      )}
      {refused && !conflict && (
        <p role="alert" className="text-sm">
          Those channels could not be held. Nothing was changed; check the
          selection.
        </p>
      )}
      {unknown && (
        <p role="alert" className="text-sm">
          The hold may not have been saved. Reload this page to see whether it
          was.
        </p>
      )}
    </section>
  );
}

export default function CustodyPage() {
  const query = useQuery({
    queryKey: ["custody-holds"],
    queryFn: listCustodyHolds,
    retry: false,
  });
  if (query.isLoading) return <p role="status">Loading holds…</p>;
  if (query.isError || !query.data) {
    return (
      <div className="space-y-2">
        <h1 className="text-lg font-semibold">Endpoint holds</h1>
        <p role="alert" className="text-sm">
          Holds are unavailable. This is not an all-clear; nothing is known to
          be released.
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
      <HoldAChannel />
    </div>
  );
}
