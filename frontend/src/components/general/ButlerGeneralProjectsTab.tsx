// ---------------------------------------------------------------------------
// ButlerGeneralProjectsTab — bu-jv1moa.12
//
// Owner projects on the General butler detail page: owner-authored,
// time-bounded charters that group links to existing records.  A project
// grants no authority: this surface schedules, arms, wakes and runs nothing,
// so it carries no Arm or Run control.
//
// Layout: project rail (left) + selected project (right), selection in
// `?project=`.  Linked records are grouped by kind; each group resolves its
// links through that record's own reader and degrades on its own.
// ---------------------------------------------------------------------------

import { useId, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { Plus, X } from "lucide-react";

import { ApiError } from "@/api/index.ts";
import type {
  GeneralProjectCharterInput,
  GeneralProjectDetail,
  GeneralProjectKind,
  GeneralProjectLink,
  GeneralProjectLinkKind,
  GeneralProjectState,
  GeneralProjectSummary,
} from "@/api/index.ts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section, SectionContent, SectionHeader, SectionTitle } from "@/components/ui/Section";
import { Textarea } from "@/components/ui/textarea";
import { Time } from "@/components/ui/time";
import { EmptyLine, ErrorLine, KV, LoadingLine } from "@/components/ui/atoms";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import {
  type LinkResolution,
  useAddGeneralProjectLink,
  useCloseGeneralProject,
  useCreateGeneralProject,
  useEditGeneralProjectCharter,
  useGeneralProject,
  useGeneralProjects,
  useProjectLinkResolution,
  useRemoveGeneralProjectLink,
} from "@/hooks/use-general-projects";

const KIND_LABEL: Record<GeneralProjectKind, string> = {
  project: "Project",
  responsibility: "Responsibility",
  decision: "Decision",
};

const LINK_LABEL: Record<GeneralProjectLinkKind, string> = {
  deadline: "Deadlines",
  owner_condition: "Owner conditions",
  trip: "Trips",
  drive_file: "Drive files",
  calendar_event: "Calendar events",
};

const LINK_ORDER: GeneralProjectLinkKind[] = [
  "deadline",
  "owner_condition",
  "trip",
  "drive_file",
  "calendar_event",
];

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "The request failed.";
}

function StateBadge({ state }: { state: GeneralProjectState }) {
  return (
    <Badge variant={state === "draft" ? "secondary" : "outline"} data-testid="project-state">
      {state}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Charter form (create and edit)
// ---------------------------------------------------------------------------

/** `datetime-local` value for an ISO instant, in the browser's zone. */
function toLocalInput(iso: string): string {
  const date = new Date(iso);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

interface CharterFormProps {
  initial?: GeneralProjectDetail;
  submitLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: (charter: GeneralProjectCharterInput, kind: GeneralProjectKind) => void;
  onCancel: () => void;
}

function CharterForm({ initial, submitLabel, pending, error, onSubmit, onCancel }: CharterFormProps) {
  const id = useId();
  const charter = initial?.charter;
  const [kind, setKind] = useState<GeneralProjectKind>(initial?.kind ?? "project");
  const [title, setTitle] = useState(charter?.title ?? "");
  const [horizon, setHorizon] = useState(charter ? toLocalInput(charter.horizon_end) : "");
  const [objective, setObjective] = useState(charter?.objective ?? "");
  const [done, setDone] = useState(charter?.done_condition ?? "");
  const [amount, setAmount] = useState(charter?.constraints.budget?.amount ?? "");
  const [currency, setCurrency] = useState(charter?.constraints.budget?.currency ?? "SGD");
  const [mustKeep, setMustKeep] = useState((charter?.constraints.must_keep ?? []).join("\n"));

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit(
      {
        title: title.trim(),
        horizon_end: new Date(horizon).toISOString(),
        objective: objective.trim(),
        done_condition: done.trim(),
        constraints: {
          budget: amount.trim() ? { amount: amount.trim(), currency: currency.trim() } : null,
          must_keep: mustKeep
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean),
        },
      },
      kind,
    );
  }

  return (
    <form className="space-y-3" onSubmit={submit} aria-label={submitLabel}>
      {!initial && (
        <div className="space-y-1">
          <Label htmlFor={`${id}-kind`}>Kind</Label>
          <select
            id={`${id}-kind`}
            className="border-input h-9 w-full rounded-md border bg-transparent px-2 text-sm"
            value={kind}
            onChange={(e) => setKind(e.target.value as GeneralProjectKind)}
          >
            {Object.entries(KIND_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="space-y-1">
        <Label htmlFor={`${id}-title`}>Title</Label>
        <Input
          id={`${id}-title`}
          required
          maxLength={200}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-horizon`}>Horizon (at most 180 days)</Label>
        <Input
          id={`${id}-horizon`}
          type="datetime-local"
          required
          value={horizon}
          onChange={(e) => setHorizon(e.target.value)}
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-objective`}>Objective</Label>
        <Textarea
          id={`${id}-objective`}
          required
          maxLength={1000}
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-done`}>Done when</Label>
        <Textarea
          id={`${id}-done`}
          required
          maxLength={1000}
          value={done}
          onChange={(e) => setDone(e.target.value)}
        />
      </div>
      <div className="grid grid-cols-[1fr_6rem] gap-2">
        <div className="space-y-1">
          <Label htmlFor={`${id}-amount`}>Budget (optional, descriptive)</Label>
          <Input
            id={`${id}-amount`}
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor={`${id}-currency`}>Currency</Label>
          <Input
            id={`${id}-currency`}
            maxLength={3}
            value={currency}
            onChange={(e) => setCurrency(e.target.value.toUpperCase())}
          />
        </div>
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-keep`}>Must keep (one per line)</Label>
        <Textarea id={`${id}-keep`} value={mustKeep} onChange={(e) => setMustKeep(e.target.value)} />
      </div>
      {error != null && <ErrorLine>{errorText(error)}</ErrorLine>}
      <div className="flex gap-2">
        <Button type="submit" disabled={pending}>
          {pending ? "Saving…" : submitLabel}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function CreateProject({ onCreated, onCancel }: { onCreated: (id: string) => void; onCancel: () => void }) {
  const create = useCreateGeneralProject();
  // One key per form: a double submit or retry replays instead of duplicating.
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  return (
    <Section data-testid="project-create">
      <SectionHeader>
        <SectionTitle>New project</SectionTitle>
      </SectionHeader>
      <SectionContent>
        <CharterForm
          submitLabel="Create draft"
          pending={create.isPending}
          error={create.error}
          onCancel={onCancel}
          onSubmit={(charter, kind) =>
            create.mutate(
              { ...charter, kind, idempotency_key: idempotencyKey },
              { onSuccess: (response) => onCreated(response.data.id) },
            )
          }
        />
      </SectionContent>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Linked records
// ---------------------------------------------------------------------------

function ResolvedLine({ resolution }: { resolution: LinkResolution | undefined }) {
  if (!resolution || resolution.state === "loading") return <LoadingLine />;
  if (resolution.state === "resolved") {
    return (
      <span className="min-w-0">
        <span className="block truncate text-sm">{resolution.label}</span>
        {resolution.detail && (
          <span className="block truncate text-xs text-muted-foreground">{resolution.detail}</span>
        )}
      </span>
    );
  }
  const text =
    resolution.state === "missing"
      ? "Not found: deleted or never existed"
      : resolution.state === "no_reader"
        ? "No reader for this record kind yet"
        : `Unavailable: ${resolution.reason}`;
  return <span className="text-xs text-muted-foreground">{text}</span>;
}

interface LinkRegionProps {
  kind: GeneralProjectLinkKind;
  links: GeneralProjectLink[];
  resolutions: Map<string, LinkResolution>;
  editable: boolean;
  onRemove: (linkId: string) => void;
  removing: boolean;
}

function LinkRegion({ kind, links, resolutions, editable, onRemove, removing }: LinkRegionProps) {
  const failed = links.some((l) => resolutions.get(l.id)?.state === "unavailable");
  return (
    <div data-testid={`link-region-${kind}`} className="space-y-1.5">
      <h4 className="font-mono text-[11px] uppercase tracking-[0.10em] text-muted-foreground">
        {LINK_LABEL[kind]}
      </h4>
      {failed && <SourceDegradedNote label={LINK_LABEL[kind]} detail="reader unavailable" />}
      <ul className="space-y-1">
        {links.map((link) => (
          <li key={link.id} className="flex items-start justify-between gap-2">
            <span className="min-w-0 space-y-0.5">
              <ResolvedLine resolution={resolutions.get(link.id)} />
              <span className="block font-mono text-[11px] text-muted-foreground">{link.ref_id}</span>
            </span>
            {editable && (
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Unlink ${link.ref_id}`}
                disabled={removing}
                onClick={() => onRemove(link.id)}
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </Button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function AddLink({ projectId }: { projectId: string }) {
  const id = useId();
  const add = useAddGeneralProjectLink(projectId);
  const [kind, setKind] = useState<GeneralProjectLinkKind>("trip");
  const [refId, setRefId] = useState("");
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      aria-label="Link a record"
      onSubmit={(event) => {
        event.preventDefault();
        add.mutate({ ref_kind: kind, ref_id: refId.trim() }, { onSuccess: () => setRefId("") });
      }}
    >
      <div className="space-y-1">
        <Label htmlFor={`${id}-kind`}>Record kind</Label>
        <select
          id={`${id}-kind`}
          className="border-input h-9 rounded-md border bg-transparent px-2 text-sm"
          value={kind}
          onChange={(e) => setKind(e.target.value as GeneralProjectLinkKind)}
        >
          {LINK_ORDER.map((value) => (
            <option key={value} value={value}>
              {LINK_LABEL[value]}
            </option>
          ))}
        </select>
      </div>
      <div className="min-w-48 flex-1 space-y-1">
        <Label htmlFor={`${id}-ref`}>Record id</Label>
        <Input id={`${id}-ref`} required value={refId} onChange={(e) => setRefId(e.target.value)} />
      </div>
      <Button type="submit" variant="outline" disabled={add.isPending}>
        Link
      </Button>
      {add.error != null && <ErrorLine className="basis-full">{errorText(add.error)}</ErrorLine>}
    </form>
  );
}

// ---------------------------------------------------------------------------
// Selected project
// ---------------------------------------------------------------------------

function ProjectDetailPanel({ projectId }: { projectId: string }) {
  const query = useGeneralProject(projectId);
  const [editing, setEditing] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const edit = useEditGeneralProjectCharter(projectId);
  const close = useCloseGeneralProject(projectId);
  const remove = useRemoveGeneralProjectLink(projectId);
  const project = query.data?.data;
  const resolutions = useProjectLinkResolution(project?.links ?? []);

  if (query.isPending) return <LoadingLine />;
  if (query.isError || !project) {
    return <ErrorLine>Could not load this project. {errorText(query.error)}</ErrorLine>;
  }

  const draft = project.state === "draft";
  const { charter } = project;
  const budget = charter.constraints.budget;
  const groups = LINK_ORDER.map((kind) => ({
    kind,
    links: project.links.filter((l) => l.ref_kind === kind),
  })).filter((group) => group.links.length > 0);

  return (
    <div className="space-y-4" data-testid="project-detail">
      <Section>
        <SectionHeader>
          <SectionTitle>{project.title}</SectionTitle>
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            {KIND_LABEL[project.kind]} · v{project.current_charter_version}
            <StateBadge state={project.state} />
          </span>
        </SectionHeader>
        <SectionContent>
          {editing ? (
            <CharterForm
              initial={project}
              submitLabel="Save new version"
              pending={edit.isPending}
              error={edit.error}
              onCancel={() => setEditing(false)}
              onSubmit={(next) =>
                edit.mutate(
                  { ...next, expected_version: project.current_charter_version },
                  { onSuccess: () => setEditing(false) },
                )
              }
            />
          ) : (
            <div className="space-y-3">
              <KV k="Horizon" v={<Time value={charter.horizon_end} mode="absolute" />} />
              {project.closed_at && (
                <KV
                  k={project.state === "expired" ? "Expired" : "Closed"}
                  v={<Time value={project.closed_at} mode="absolute" />}
                />
              )}
              <div>
                <p className="text-xs text-muted-foreground">Objective</p>
                <p className="whitespace-pre-wrap text-sm">{charter.objective}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Done when</p>
                <p className="whitespace-pre-wrap text-sm">{charter.done_condition}</p>
              </div>
              {budget && <KV k="Budget" v={`${budget.amount} ${budget.currency}`} mono />}
              {charter.constraints.must_keep.length > 0 && (
                <div>
                  <p className="text-xs text-muted-foreground">Must keep</p>
                  <ul className="list-disc pl-5 text-sm">
                    {charter.constraints.must_keep.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}
              <p className="text-xs text-muted-foreground">
                A project records intent only; it grants no authority and runs nothing.
              </p>
              {draft && (
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" onClick={() => setEditing(true)}>
                    Edit charter
                  </Button>
                  {confirmClose ? (
                    <>
                      <Button
                        variant="destructive"
                        disabled={close.isPending}
                        onClick={() => close.mutate(project.current_charter_version)}
                      >
                        Confirm close
                      </Button>
                      <Button variant="ghost" onClick={() => setConfirmClose(false)}>
                        Keep open
                      </Button>
                    </>
                  ) : (
                    <Button variant="ghost" onClick={() => setConfirmClose(true)}>
                      Close project
                    </Button>
                  )}
                </div>
              )}
              {close.error != null && <ErrorLine>{errorText(close.error)}</ErrorLine>}
            </div>
          )}
        </SectionContent>
      </Section>

      <Section data-testid="project-links">
        <SectionHeader>
          <SectionTitle>Linked records</SectionTitle>
        </SectionHeader>
        <SectionContent className="space-y-4">
          {groups.length === 0 ? (
            <EmptyLine>No linked records.</EmptyLine>
          ) : (
            groups.map((group) => (
              <LinkRegion
                key={group.kind}
                kind={group.kind}
                links={group.links}
                resolutions={resolutions}
                editable={draft}
                removing={remove.isPending}
                onRemove={(linkId) => remove.mutate(linkId)}
              />
            ))
          )}
          {remove.error != null && <ErrorLine>{errorText(remove.error)}</ErrorLine>}
          {draft && <AddLink projectId={project.id} />}
        </SectionContent>
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab
// ---------------------------------------------------------------------------

function ProjectRail({
  projects,
  selected,
  onSelect,
}: {
  projects: GeneralProjectSummary[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <ul className="space-y-1" aria-label="Projects">
      {projects.map((project) => (
        <li key={project.id}>
          <button
            type="button"
            aria-current={project.id === selected ? "true" : undefined}
            onClick={() => onSelect(project.id)}
            className="w-full rounded-sm px-2 py-1.5 text-left hover:bg-muted aria-[current=true]:bg-muted"
          >
            <span className="block truncate text-sm">{project.title}</span>
            <span className="flex items-center gap-2 text-xs text-muted-foreground">
              {KIND_LABEL[project.kind]} · <Time value={project.horizon_end} mode="absolute" />
              {project.state !== "draft" && <StateBadge state={project.state} />}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

export default function ButlerGeneralProjectsTab() {
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = useState(false);
  const list = useGeneralProjects();
  const selected = params.get("project");

  function select(id: string) {
    setCreating(false);
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("project", id);
        return next;
      },
      { replace: true },
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[18rem_1fr]" data-testid="general-projects-tab">
      <Section>
        <SectionHeader>
          <SectionTitle>Projects</SectionTitle>
          <Button size="sm" variant="outline" onClick={() => setCreating(true)}>
            <Plus className="h-3.5 w-3.5" aria-hidden="true" /> New
          </Button>
        </SectionHeader>
        <SectionContent>
          {list.isPending ? (
            <LoadingLine />
          ) : list.isError ? (
            <ErrorLine>Could not load projects. {errorText(list.error)}</ErrorLine>
          ) : list.data.data.length === 0 ? (
            <EmptyLine>No projects yet.</EmptyLine>
          ) : (
            <ProjectRail projects={list.data.data} selected={selected} onSelect={select} />
          )}
        </SectionContent>
      </Section>
      <div>
        {creating ? (
          <CreateProject onCreated={select} onCancel={() => setCreating(false)} />
        ) : selected ? (
          <ProjectDetailPanel key={selected} projectId={selected} />
        ) : (
          <EmptyLine>
            Select a project, or create one: a charter with an objective, a done condition and a
            horizon. Projects group links to existing records and grant no authority.
          </EmptyLine>
        )}
      </div>
    </div>
  );
}
