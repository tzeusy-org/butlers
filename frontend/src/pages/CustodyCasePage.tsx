import { Link, useParams } from "react-router";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getCustodyCase, listCustodyHolds } from "@/api/custody.ts";
import type { CaseEvidence } from "@/api/custody.ts";
import { Button } from "@/components/ui/button";
import { Time } from "@/components/ui/time";

// ---------------------------------------------------------------------------
// CustodyCasePage — route /custody/:episodeId
//
// One lost-device recovery case: the owner-followed checklist (SIM/carrier and account sign-out)
// with the provider recovery doors, what the case's contributors have found, and the holds that
// belong to it. The checklist is instructions the owner follows; ticking a step is descriptive
// progress kept in this browser. It never releases a hold, answers a security question, approves
// anything or contacts a provider. Release and replace stay on /custody. A failed read says
// unavailable, never clear; evidence is context and never proves anything either way.
// ---------------------------------------------------------------------------

const CHECKLIST: { id: string; title: string; detail: string }[] = [
  {
    id: "sim-lock",
    title: "Call your carrier and suspend or lock the SIM",
    detail: "Ask them to block the line and to flag it against SIM swap and number port-out.",
  },
  {
    id: "sim-replace",
    title: "Order a replacement SIM and set a carrier account PIN",
    detail: "Do this before moving any two-step codes back to the number.",
  },
  {
    id: "signout-google",
    title: "Sign the lost device out of your Google account",
    detail: "Review devices and recent security activity, then change the password if unsure.",
  },
  {
    id: "signout-microsoft",
    title: "Sign the lost device out of your Microsoft account",
    detail: "Review recent activity and sign out everywhere.",
  },
  {
    id: "signout-apple",
    title: "Remove the lost device from your Apple account",
    detail: "Mark it lost, then remove it from the account's device list.",
  },
  {
    id: "signout-messaging",
    title: "End messaging sessions on the lost device",
    detail: "Use linked devices in Telegram and WhatsApp on a device you trust.",
  },
];

const DOORS: { label: string; href: string }[] = [
  { label: "Google account activity", href: "https://myaccount.google.com/notification/recovery" },
  { label: "Microsoft account activity", href: "https://account.live.com/Activity" },
  { label: "Apple account recovery", href: "https://iforgot.apple.com/" },
];

const CONTRIBUTOR: Record<string, string> = {
  endpoint_custody: "Custody",
  finance: "Finance",
  chronicler: "Owner device",
};

const KIND: Record<string, string> = {
  hold: "Channel held",
  release: "Channel released",
  replace: "Channel replaced",
  held_window: "Held window so far",
  held_window_final: "Held window, final",
  device_last_fix: "Last known device fix",
};

function loadTicks(key: string): Set<string> {
  try {
    const raw = window.localStorage.getItem(key);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : []);
  } catch {
    return new Set();
  }
}

function saveTicks(key: string, ticks: Set<string>): void {
  try {
    window.localStorage.setItem(key, JSON.stringify([...ticks]));
  } catch {
    /* progress is a convenience; losing it is harmless */
  }
}

function Checklist({ episodeId }: { episodeId: string }) {
  const key = `custody-checklist:${episodeId}`;
  const [ticks, setTicks] = useState(() => loadTicks(key));
  const toggle = (id: string) => {
    const next = new Set(ticks);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setTicks(next);
    saveTicks(key, next);
  };
  return (
    <section className="space-y-2" aria-labelledby="checklist-heading">
      <h2 id="checklist-heading" className="font-medium">
        Recovery checklist
      </h2>
      <p className="text-sm text-muted-foreground">
        Steps for you to follow. Ticking one only tracks your progress here; it does not release
        anything or answer a security question.
      </p>
      <ul className="space-y-2">
        {CHECKLIST.map((step) => (
          <li key={step.id} className="text-sm">
            <label className="flex min-h-11 items-center gap-3 font-medium">
              <input
                type="checkbox"
                checked={ticks.has(step.id)}
                aria-describedby={`${step.id}-detail`}
                onChange={() => toggle(step.id)}
              />
              {step.title}
            </label>
            <p id={`${step.id}-detail`} className="pl-7 text-muted-foreground">
              {step.detail}
            </p>
          </li>
        ))}
      </ul>
      <p className="text-sm">
        {ticks.size} of {CHECKLIST.length} steps ticked.
      </p>
      <ul className="space-y-1 text-sm">
        {DOORS.map((door) => (
          <li key={door.href}>
            <a href={door.href} target="_blank" rel="noreferrer" className="underline underline-offset-2">
              {door.label}
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}

function humanize(name: string): string {
  return name.replace(/_/g, " ");
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "unknown";
  if (typeof value === "object") {
    const total = (value as { total?: number | null }).total;
    return total === null || total === undefined ? "unknown" : String(total);
  }
  return String(value);
}

function Evidence({ item, now }: { item: CaseEvidence; now: number }) {
  const fix = item.contributor === "chronicler" && item.kind === "device_last_fix";
  // The server already reads a stale fix as unknown; this also covers a view left open past it.
  const stale =
    fix &&
    (item.summary.reason === "stale" ||
      (typeof item.summary.fresh_until === "string" && now > Date.parse(item.summary.fresh_until)));
  const entries = Object.entries(item.summary).filter(([name]) => name !== "fresh_until");
  return (
    <li className="space-y-1 rounded border p-3 text-sm">
      <p className="font-medium">
        {CONTRIBUTOR[item.contributor] ?? "Another source"}: {KIND[item.kind] ?? "Evidence"}
      </p>
      <p className="text-muted-foreground">
        Recorded <Time value={item.contributed_at} mode="absolute" />
        {stale ? " · this reading has gone stale and is unknown now" : ""}
      </p>
      {entries.length > 0 && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-3">
          {entries.map(([name, value]) => (
            <div key={name} className="contents">
              <dt className="text-muted-foreground">{humanize(name)}</dt>
              <dd>{formatValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

export default function CustodyCasePage() {
  const { episodeId = "" } = useParams();
  const caseQuery = useQuery({
    queryKey: ["custody-case", episodeId],
    queryFn: () => getCustodyCase(episodeId),
    retry: false,
  });
  const holdsQuery = useQuery({ queryKey: ["custody-holds"], queryFn: listCustodyHolds, retry: false });
  // The time this view was opened; a reading past its freshness window reads unknown from then.
  const [now] = useState(() => Date.now());
  const holds = holdsQuery.data?.filter((hold) => hold.episode_id === episodeId);
  return (
    <div className="space-y-5">
      <h1 className="text-lg font-semibold">Lost-device recovery case</h1>
      <p className="text-sm">
        <Link to="/custody" className="underline underline-offset-2">
          All holds
        </Link>
        {" · "}
        <Link to="/security" className="underline underline-offset-2">
          Security questions
        </Link>
      </p>
      <Checklist episodeId={episodeId} />
      <section className="space-y-2" aria-labelledby="holds-heading">
        <h2 id="holds-heading" className="font-medium">
          Holds in this case
        </h2>
        {holdsQuery.isLoading ? (
          <p role="status">Loading holds…</p>
        ) : holdsQuery.isError ? (
          <p role="alert" className="text-sm">
            Holds are unavailable. This is not an all-clear.
          </p>
        ) : !holds || holds.length === 0 ? (
          <p className="text-sm text-muted-foreground">No channel in this case is currently held.</p>
        ) : (
          <p className="text-sm">
            {holds.length} held. Release or replace them on <Link to="/custody" className="underline">the holds page</Link>.
          </p>
        )}
      </section>
      <section className="space-y-2" aria-labelledby="evidence-heading">
        <h2 id="evidence-heading" className="font-medium">
          What the case has found
        </h2>
        <p className="text-sm text-muted-foreground">
          Context only. None of this proves or clears anything.
        </p>
        {caseQuery.isLoading ? (
          <p role="status">Loading case…</p>
        ) : caseQuery.isError || !caseQuery.data ? (
          <div className="space-y-2">
            <p role="alert" className="text-sm">
              The case is unavailable. This is not an all-clear.
            </p>
            <Button variant="outline" onClick={() => void caseQuery.refetch()}>
              Retry
            </Button>
          </div>
        ) : (
          <>
            <p className="text-sm">
              Case {caseQuery.data.state}
              {caseQuery.data.outcome ? ` (${caseQuery.data.outcome.replace(/_/g, " ")})` : ""}
            </p>
            {caseQuery.data.evidence.length === 0 ? (
              <p className="text-sm text-muted-foreground">Nothing recorded yet.</p>
            ) : (
              <ul className="space-y-2">
                {caseQuery.data.evidence.map((item) => (
                  <Evidence
                    key={`${item.contributor}:${item.kind}:${item.contributed_at}`}
                    item={item}
                    now={now}
                  />
                ))}
              </ul>
            )}
            {caseQuery.data.truncated && (
              <p className="text-sm text-muted-foreground">Showing the newest entries only.</p>
            )}
          </>
        )}
      </section>
    </div>
  );
}
