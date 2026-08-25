import { Badge } from "@/components/ui/badge";
import type { EducationSourceMaterial } from "@/api/index.ts";
import {
  indexSources,
  parseSourceRefs,
  resolveSourceRef,
  type RegistryStatus,
  type ResolvedSourceRef,
} from "./source-annotations";

/**
 * Source annotations for one mind map node (bu-istke.5).
 *
 * The education butler never fetches or reads source material — the manifesto
 * is explicit that source material is "owner-provided or model-recalled". So a
 * `source_refs` entry is one of two very different things, and this panel's
 * only real job is making sure a reader cannot confuse them:
 *
 * - a **referenced** location, taken from a source the owner registered, or
 * - a **model-recalled** location, produced from the model's own knowledge.
 *
 * Every weaker state therefore carries its qualifier as leading, plain-language
 * text (never colour alone, and never an abbreviation), and only the
 * `referenced` state renders anything that looks like a citation: a resolved
 * title, and a link when the registered record has a URL. A ref whose registry
 * lookup misses or could not run shows no title and no link at all — it says
 * what went wrong instead of degrading into something citation-shaped.
 */

/** Colour + wording per resolution state. State colour is text/border only. */
const STATE_STYLE: Record<ResolvedSourceRef["state"], string> = {
  referenced: "border-[var(--border-strong)] text-[var(--mfg)]",
  "model-recalled": "border-[var(--amber)]/50 text-[var(--amber-text)]",
  unregistered: "border-[var(--border-strong)] text-[var(--dim)]",
  unresolved: "border-[var(--border-strong)] text-[var(--dim)]",
};

const STATE_LABEL: Record<ResolvedSourceRef["state"], string> = {
  referenced: "Referenced",
  "model-recalled": "Model-recalled",
  unregistered: "Source no longer registered",
  unresolved: "Not checked against the registry",
};

function stateCaption(
  state: ResolvedSourceRef["state"],
  registryStatus: RegistryStatus,
): string | null {
  switch (state) {
    case "referenced":
      // The strong state is the unmarked one: no caveat to add, and adding
      // filler here would blunt the caveats that matter below.
      return null;
    case "model-recalled":
      return "Recalled from the model's own knowledge. The butler did not read the source, so treat this location as unverified.";
    case "unregistered":
      return "This reference names a source that is no longer registered. It cannot be resolved to a title and is not a citation.";
    case "unresolved":
      return registryStatus === "loading"
        ? "Checking this reference against the source registry."
        : "The source registry could not be reached, so this reference has not been checked against it.";
  }
}

function SourceRefRow({
  resolved,
  registryStatus,
}: {
  resolved: ResolvedSourceRef;
  registryStatus: RegistryStatus;
}) {
  const { ref, state, source } = resolved;
  const caption = stateCaption(state, registryStatus);

  return (
    <li className="space-y-1 border-t border-[var(--border-soft)] pt-3 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-baseline gap-2">
        <Badge variant="outline" className={STATE_STYLE[state]}>
          {STATE_LABEL[state]}
        </Badge>
        {state === "referenced" && source && (
          <span className="text-sm font-medium">{source.title}</span>
        )}
        {state === "model-recalled" && source && (
          <span className="text-sm font-medium">Recalled from {source.title}</span>
        )}
        {state === "model-recalled" && !source && (
          <span className="text-sm text-muted-foreground">No registered source named</span>
        )}
      </div>

      {ref.location ? (
        <p className="font-mono text-xs text-muted-foreground">{ref.location}</p>
      ) : (
        <p className="text-xs text-muted-foreground">Location not recorded</p>
      )}

      {/* The raw ID is the only actionable handle left on a ref the registry
          cannot resolve — the owner needs it to re-register or remove it. */}
      {(state === "unregistered" || state === "unresolved") && ref.sourceId && (
        <p className="font-mono text-xs text-[var(--dim)]">Reference ID: {ref.sourceId}</p>
      )}

      {caption && <p className="text-xs text-muted-foreground">{caption}</p>}

      {ref.note && <p className="text-xs text-muted-foreground">{ref.note}</p>}

      {/* Only a resolved, source-read reference gets a follow-the-citation
          affordance. */}
      {state === "referenced" && source?.url && (
        <a
          href={source.url}
          target="_blank"
          rel="noreferrer"
          className="text-xs underline underline-offset-4"
        >
          Open registered source
        </a>
      )}
    </li>
  );
}

interface NodeSourceAnnotationsProps {
  /** The node's raw `metadata` bag. */
  metadata: Record<string, unknown> | null | undefined;
  /** Registry records, when the lookup resolved. */
  sources: EducationSourceMaterial[] | undefined;
  registryStatus: RegistryStatus;
}

export default function NodeSourceAnnotations({
  metadata,
  sources,
  registryStatus,
}: NodeSourceAnnotationsProps) {
  const refs = parseSourceRefs(metadata);
  // A node with no annotations renders exactly as it did before this feature.
  if (refs.length === 0) return null;

  const registry = indexSources(sources);

  return (
    <div className="border-t pt-4">
      <h4 className="mb-2 text-sm font-medium">Sources</h4>
      <ul className="space-y-3">
        {refs.map((ref, index) => (
          <SourceRefRow
            // Refs carry no stable identity of their own, and the same source
            // can legitimately appear twice at different locations.
            key={`${ref.sourceId ?? "unsourced"}:${ref.location ?? ""}:${index}`}
            resolved={resolveSourceRef(ref, registry, registryStatus)}
            registryStatus={registryStatus}
          />
        ))}
      </ul>
    </div>
  );
}
