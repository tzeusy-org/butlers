/**
 * TanStack Query hooks for General owner projects (bu-jv1moa.12).
 *
 * Projects are owner-authored charters; writes go only through the owner's
 * dashboard session.  Typed links are resolved here, in the browser, through
 * each record's own owner-authorized reader — General never reads another
 * butler's schema.  A reader that fails marks only its own region unavailable.
 */

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  addGeneralProjectLink,
  closeGeneralProject,
  createGeneralProject,
  editGeneralProjectCharter,
  getButlerDeadline,
  getCalendarWorkspaceEntry,
  getGeneralProject,
  getGeneralDriveFile,
  getGeneralProjects,
  getSystemConditions,
  getTravelTripSummary,
  removeGeneralProjectLink,
} from "@/api/index.ts";
import type {
  ApiResponse,
  GeneralProjectCharterInput,
  GeneralProjectCreateRequest,
  GeneralProjectDetail,
  GeneralProjectLink,
  GeneralProjectLinkKind,
} from "@/api/index.ts";

const PROJECTS_KEY = ["general-projects"] as const;

export function useGeneralProjects() {
  return useQuery({ queryKey: PROJECTS_KEY, queryFn: () => getGeneralProjects() });
}

export function useGeneralProject(projectId: string | null) {
  return useQuery({
    queryKey: [...PROJECTS_KEY, projectId],
    queryFn: () => getGeneralProject(projectId as string),
    enabled: projectId != null,
  });
}

/** Every write returns the fresh detail: seed it, then refresh the list. */
function useProjectWrite<V>(
  write: (variables: V) => Promise<ApiResponse<GeneralProjectDetail>>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: write,
    onSuccess: (response) => {
      queryClient.setQueryData([...PROJECTS_KEY, response.data.id], response);
      void queryClient.invalidateQueries({ queryKey: PROJECTS_KEY, exact: true });
    },
  });
}

export function useCreateGeneralProject() {
  return useProjectWrite((body: GeneralProjectCreateRequest) => createGeneralProject(body));
}

export function useEditGeneralProjectCharter(projectId: string) {
  return useProjectWrite((body: GeneralProjectCharterInput & { expected_version: number }) =>
    editGeneralProjectCharter(projectId, body),
  );
}

export function useCloseGeneralProject(projectId: string) {
  return useProjectWrite((expectedVersion: number) =>
    closeGeneralProject(projectId, expectedVersion),
  );
}

export function useAddGeneralProjectLink(projectId: string) {
  return useProjectWrite((link: { ref_kind: GeneralProjectLinkKind; ref_id: string }) =>
    addGeneralProjectLink(projectId, link),
  );
}

export function useRemoveGeneralProjectLink(projectId: string) {
  return useProjectWrite((linkId: string) => removeGeneralProjectLink(projectId, linkId));
}

// ---------------------------------------------------------------------------
// Link resolution through canonical readers
// ---------------------------------------------------------------------------

export type LinkResolution =
  | { state: "loading" }
  | { state: "resolved"; label: string; detail: string | null }
  | { state: "missing" }
  | { state: "unavailable"; reason: string };

function failure(error: unknown): LinkResolution {
  if (error instanceof ApiError && error.status === 404) return { state: "missing" };
  return { state: "unavailable", reason: "reader failed" };
}

/** Kinds with a per-record reader; owner conditions resolve from one ledger read. */
type SingleKind = Exclude<GeneralProjectLinkKind, "owner_condition">;

const SINGLE_READERS: Record<SingleKind, (refId: string) => Promise<LinkResolution>> = {
  trip: async (refId) => {
    const trip = (await getTravelTripSummary(refId)).trip;
    return {
      state: "resolved",
      label: trip.name,
      detail: `${trip.destination} · ${trip.start_date} to ${trip.end_date}`,
    };
  },
  calendar_event: async (refId) => {
    const entry = (await getCalendarWorkspaceEntry(refId)).data;
    return { state: "resolved", label: entry.title, detail: entry.start_at };
  },
  // A deadline link is `<butler>:<task id>`: it lives in that butler's schedule.
  deadline: async (refId) => {
    const separator = refId.indexOf(":");
    if (separator < 1) return { state: "missing" };
    const deadline = (
      await getButlerDeadline(refId.slice(0, separator), refId.slice(separator + 1))
    ).data;
    return {
      state: "resolved",
      label: deadline.display_title ?? deadline.name,
      detail: `${deadline.butler} · due ${deadline.target_date}${
        deadline.deadline_status ? ` · ${deadline.deadline_status}` : ""
      }`,
    };
  },
  drive_file: async (refId) => {
    const file = (await getGeneralDriveFile(refId)).data;
    return { state: "resolved", label: file.name, detail: file.mime_type };
  },
};

/** Resolve each link through its record's reader; returns a resolution per link id. */
export function useProjectLinkResolution(
  links: GeneralProjectLink[],
): Map<string, LinkResolution> {
  const single = links.filter(
    (l): l is GeneralProjectLink & { ref_kind: SingleKind } => l.ref_kind !== "owner_condition",
  );
  const needsConditions = links.some((l) => l.ref_kind === "owner_condition");

  const singles = useQueries({
    queries: single.map((link) => ({
      queryKey: ["project-link", link.ref_kind, link.ref_id],
      queryFn: () => SINGLE_READERS[link.ref_kind](link.ref_id),
      retry: false,
    })),
  });

  const conditions = useQuery({
    queryKey: ["project-link", "owner_condition", "ledger"],
    queryFn: () => getSystemConditions({ ledger: "owner", limit: 200 }),
    enabled: needsConditions,
    retry: false,
  });

  const resolved = new Map<string, LinkResolution>();
  single.forEach((link, index) => {
    const query = singles[index];
    if (query.isPending) resolved.set(link.id, { state: "loading" });
    else if (query.isError) resolved.set(link.id, failure(query.error));
    else resolved.set(link.id, query.data);
  });
  for (const link of links) {
    if (link.ref_kind === "owner_condition") {
      if (conditions.isPending) {
        resolved.set(link.id, { state: "loading" });
      } else if (conditions.isError || !conditions.data.data.conditions_available) {
        resolved.set(link.id, { state: "unavailable", reason: "reader failed" });
      } else {
        const facts = conditions.data.data;
        const match = facts.conditions.find((c) => c.id === link.ref_id);
        if (match) {
          resolved.set(link.id, {
            state: "resolved",
            label: match.summary ?? match.source,
            detail: match.state,
          });
        } else if (facts.total <= facts.conditions.length) {
          resolved.set(link.id, { state: "missing" });
        } else {
          resolved.set(link.id, { state: "unavailable", reason: "beyond the reader's page" });
        }
      }
    }
  }
  return resolved;
}
