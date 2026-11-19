import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  bindCustodyDevice,
  getCustodyDevice,
  listCustodyDevices,
} from "@/api/custody.ts";
import type { DeviceFix } from "@/api/custody.ts";
import { ApiError } from "@/api/client.ts";
import { Button } from "@/components/ui/button";

// ---------------------------------------------------------------------------
// CustodyDevicePanel — which of the owner's OwnTracks devices is the one in question.
//
// The owner picks one registered endpoint; the server records it as case evidence and the
// Chronicler reports that device's last fix as a named place and an age, never coordinates. This
// is context only: it never answers a security question and never changes a hold. A stale or
// missing fix reads unknown, and a failed read says unavailable, never "no device" or "all clear".
// Binding is idempotent on the server (the same endpoint again records nothing), so there is no
// command id to mint; a retry after an unknown outcome is simply the same selection.
// ---------------------------------------------------------------------------

const UNKNOWN_REASON: Record<string, string> = {
  not_observed_yet: "No fix has been seen from this device yet.",
  stale: "The last fix is too old to trust.",
};

function formatAge(seconds: number): string {
  if (seconds < 90) return "moments ago";
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `${minutes} minutes ago`;
  return `${Math.round(minutes / 60)} hours ago`;
}

function FixLine({ fix }: { fix: DeviceFix | null }) {
  if (fix === null) return <p className="text-sm">No fix has been read yet.</p>;
  if (fix.status === "known") {
    return (
      <p className="text-sm">
        Last seen{" "}
        {fix.age_seconds === undefined
          ? "recently"
          : formatAge(fix.age_seconds)}
        {fix.place ? ` near ${fix.place}` : ", place not named"}.
      </p>
    );
  }
  return (
    <p className="text-sm">
      Location unknown.{" "}
      {UNKNOWN_REASON[fix.reason ?? ""] ?? "No trustworthy fix is available."}
    </p>
  );
}

export function CustodyDevicePanel({
  episodeId,
  open,
}: {
  episodeId: string;
  /** True only when the case is known to be open; anything else offers no binding. */
  open: boolean;
}) {
  const queryClient = useQueryClient();
  const device = useQuery({
    queryKey: ["custody-device", episodeId],
    queryFn: () => getCustodyDevice(episodeId),
    retry: false,
  });
  const candidates = useQuery({
    queryKey: ["custody-device-candidates", episodeId],
    queryFn: () => listCustodyDevices(episodeId),
    enabled: open,
    retry: false,
  });
  const [choice, setChoice] = useState("");
  const bind = useMutation({
    mutationFn: (endpoint: string) => bindCustodyDevice(episodeId, endpoint),
    gcTime: 0,
    onSuccess: () => setChoice(""),
    onSettled: () =>
      queryClient.invalidateQueries({
        queryKey: ["custody-device", episodeId],
      }),
  });
  const refused =
    bind.error instanceof ApiError &&
    bind.error.status >= 400 &&
    bind.error.status < 500;
  const unknown = bind.error !== null && !refused;
  return (
    <section className="space-y-2" aria-labelledby="device-heading">
      <h2 id="device-heading" className="font-medium">
        Your device
      </h2>
      <p className="text-sm text-muted-foreground">
        Pick the OwnTracks device that is lost or in question. Context only: it
        shows a place and an age, never coordinates, and it never answers a
        security question.
      </p>
      {device.isLoading ? (
        <p role="status">Loading the device…</p>
      ) : device.isError || !device.data ? (
        <div className="space-y-2">
          <p role="alert" className="text-sm">
            The device is unavailable. This does not mean no device is chosen.
          </p>
          <Button variant="outline" onClick={() => void device.refetch()}>
            Retry
          </Button>
        </div>
      ) : device.data.bound ? (
        <div className="space-y-1">
          <p className="text-sm">
            Chosen device:{" "}
            <span className="font-medium">
              {device.data.endpoint_identity ?? "a device"}
            </span>
          </p>
          <FixLine fix={device.data.last_fix} />
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          No device is chosen for this case yet.
        </p>
      )}
      {open &&
        (candidates.isLoading ? (
          <p role="status">Loading your devices…</p>
        ) : candidates.isError || !candidates.data ? (
          <div className="space-y-2">
            <p role="alert" className="text-sm">
              Your devices could not be loaded, so none can be chosen from here.
            </p>
            <Button variant="outline" onClick={() => void candidates.refetch()}>
              Retry
            </Button>
          </div>
        ) : candidates.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No OwnTracks devices are registered.
          </p>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-3 text-sm">
              Device
              <select
                value={choice}
                onChange={(event) => setChoice(event.target.value)}
                className="min-h-11 rounded border px-2"
              >
                <option value="">Choose a device</option>
                {candidates.data.map((endpoint) => (
                  <option key={endpoint} value={endpoint}>
                    {endpoint}
                  </option>
                ))}
              </select>
            </label>
            <Button
              variant="outline"
              disabled={choice === "" || bind.isPending}
              onClick={() => bind.mutate(choice)}
            >
              Use this device
            </Button>
          </div>
        ))}
      {bind.isSuccess && (
        <p role="status" className="text-sm">
          Device chosen.
        </p>
      )}
      {refused && (
        <p role="alert" className="text-sm">
          That device could not be chosen. Nothing was changed; pick again.
        </p>
      )}
      {unknown && (
        <p role="alert" className="text-sm">
          The choice may not have been saved. Check the chosen device above,
          then try again.
        </p>
      )}
    </section>
  );
}
