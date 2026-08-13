/**
 * Snapshot-backed Bead detail. This page is deliberately a narrow read-only
 * projection: it never receives a raw export record and never turns external
 * references into tracker navigation.
 */

import { Link, useParams } from "react-router";
import type { ReactNode } from "react";

import { ApiError } from "@/api";
import { Page } from "@/components/ui/page";
import { Time } from "@/components/ui/time";
import { useBeadDetail } from "@/hooks/use-bead-detail";
import { beadDetailPath } from "@/lib/bead-detail";
import type { BeadDetail } from "@/api/types";

function errorDetails(error: unknown): Record<string, unknown> | null {
  if (!(error instanceof ApiError) || error.detail == null || typeof error.detail !== "object") {
    return null;
  }
  return error.detail as Record<string, unknown>;
}

function exportAsOfFromError(error: unknown): string | null {
  const exportAsOf = errorDetails(error)?.export_as_of;
  return typeof exportAsOf === "string" ? exportAsOf : null;
}

function DetailSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  const sectionId = `bead-section-${title.toLowerCase().replaceAll(" ", "-")}`;

  return (
    <section className="border-t border-border pt-4" aria-labelledby={sectionId}>
      <h2
        id={sectionId}
        className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground"
      >
        {title}
      </h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function ExportAsOf({ exportAsOf }: { exportAsOf: string | null | undefined }) {
  if (!exportAsOf) return null;
  return (
    <p
      data-testid="bead-export-as-of"
      className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground"
    >
      Snapshot export as of <Time value={exportAsOf} mode="absolute" precision="minute" compact />
    </p>
  );
}

function DetailFacts({ detail }: { detail: BeadDetail }) {
  const facts = [
    ["Status", detail.status],
    ["Priority", detail.priority == null ? null : `P${detail.priority}`],
    ["Type", detail.type],
  ].filter(([, value]) => value != null);
  const timestamps = [
    ["Created", detail.created_at],
    ["Updated", detail.updated_at],
    ["Started", detail.started_at],
    ["Closed", detail.closed_at],
    ["Due", detail.due_at],
  ].filter(([, value]) => value != null) as [string, string][];

  return (
    <>
      {facts.length > 0 && (
        <DetailSection title="Details">
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-3">
            {facts.map(([label, value]) => (
              <div key={label}>
                <dt className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                  {label}
                </dt>
                <dd className="mt-1 text-sm text-foreground">{value}</dd>
              </div>
            ))}
          </dl>
        </DetailSection>
      )}

      {detail.labels.length > 0 && (
        <DetailSection title="Labels">
          <ul className="flex flex-wrap gap-2" aria-label="Bead labels">
            {detail.labels.map((label) => (
              <li
                key={label}
                className="border border-border px-2 py-1 font-mono text-[10px] text-muted-foreground"
              >
                {label}
              </li>
            ))}
          </ul>
        </DetailSection>
      )}

      {timestamps.length > 0 && (
        <DetailSection title="Timestamps">
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
            {timestamps.map(([label, value]) => (
              <div key={label}>
                <dt className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                  {label}
                </dt>
                <dd className="mt-1 text-sm text-foreground">
                  <Time value={value} mode="absolute" precision="minute" />
                </dd>
              </div>
            ))}
          </dl>
        </DetailSection>
      )}
    </>
  );
}

function DetailBody({ detail, exportAsOf }: { detail: BeadDetail; exportAsOf: string | null | undefined }) {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <ExportAsOf exportAsOf={exportAsOf} />
      <DetailFacts detail={detail} />

      {detail.description && (
        <DetailSection title="Description">
          <p className="whitespace-pre-wrap text-sm leading-6 text-foreground">{detail.description}</p>
        </DetailSection>
      )}

      {detail.design && (
        <DetailSection title="Design">
          <p className="whitespace-pre-wrap text-sm leading-6 text-foreground">{detail.design}</p>
        </DetailSection>
      )}

      {detail.acceptance_criteria && (
        <DetailSection title="Acceptance criteria">
          <p className="whitespace-pre-wrap text-sm leading-6 text-foreground">
            {detail.acceptance_criteria}
          </p>
        </DetailSection>
      )}

      {detail.dependencies.length > 0 && (
        <DetailSection title="Dependencies">
          <ul className="divide-y divide-border" aria-label="Direct dependencies">
            {detail.dependencies.map((dependency) => (
              <li key={dependency.id} className="grid grid-cols-[1fr_auto] items-center gap-4 py-3">
                <Link
                  to={beadDetailPath(dependency.id)}
                  className="min-w-0 text-sm text-foreground underline decoration-border-strong underline-offset-4 hover:decoration-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                >
                  {dependency.title ?? dependency.id}
                </Link>
                <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                  {dependency.status ?? "unknown"}
                  {dependency.priority == null ? "" : ` · P${dependency.priority}`}
                </span>
              </li>
            ))}
          </ul>
        </DetailSection>
      )}

      {detail.external_ref && (
        <DetailSection title="External reference">
          <p
            data-testid="bead-external-ref"
            className="break-words font-mono text-xs text-muted-foreground"
          >
            {detail.external_ref}
          </p>
        </DetailSection>
      )}
    </div>
  );
}

export default function BeadDetailPage() {
  const { beadId = "" } = useParams<{ beadId: string }>();
  const { data, isLoading, isError, error, refetch } = useBeadDetail(beadId || null);
  const detail = data?.data;
  const unavailable =
    isError && error instanceof ApiError && error.code === "BEAD_SNAPSHOT_UNAVAILABLE";
  const notFound = isError && error instanceof ApiError && error.code === "BEAD_NOT_FOUND";
  const exportAsOf = data?.meta.export_as_of ?? exportAsOfFromError(error);
  const title = detail?.title ?? "Bead";

  return (
    <Page
      archetype="detail"
      title={title}
      description={detail?.id}
      breadcrumbs={[{ label: "Decisions", href: "/decisions" }, { label: detail?.id ?? beadId }]}
      status={
        detail?.status ? (
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            {detail.status}
          </span>
        ) : undefined
      }
      loading={isLoading}
      error={isError && !unavailable && !notFound ? error : null}
      onRetry={() => void refetch()}
      empty={
        notFound
          ? {
              title: "Bead not found",
              description: "This Bead is not present in the current readable snapshot.",
            }
          : null
      }
    >
      {unavailable ? (
        <section
          data-testid="bead-snapshot-unavailable"
          role="alert"
          aria-live="polite"
          className="border-t border-[var(--amber)] pt-4"
        >
          <p className="text-sm text-foreground">Bead snapshot is unavailable.</p>
          <p className="mt-2 text-sm text-muted-foreground">
            The detail cannot be verified until the exported snapshot is readable and fresh.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-4">
            <ExportAsOf exportAsOf={exportAsOf} />
            <button
              type="button"
              onClick={() => void refetch()}
              className="border border-border px-3 py-2 font-mono text-[10px] uppercase tracking-[0.14em] text-foreground hover:border-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
            >
              Retry
            </button>
          </div>
        </section>
      ) : detail ? (
        <DetailBody detail={detail} exportAsOf={exportAsOf} />
      ) : null}
    </Page>
  );
}
