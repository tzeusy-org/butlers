import { useParams } from "react-router";

import EntityDetailView from "@/components/relationship/EntityDetailView";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { Skeleton } from "@/components/ui/skeleton";
import { useRelationshipEntity } from "@/hooks/use-entities";

// ---------------------------------------------------------------------------
// RelationshipEntityDetailPage
// ---------------------------------------------------------------------------

export default function RelationshipEntityDetailPage() {
  const { entityId } = useParams<{ entityId: string }>();
  const { data: entity, isLoading, error } = useRelationshipEntity(entityId);

  return (
    <div className="space-y-6">
      {/* Breadcrumbs */}
      <Breadcrumbs
        items={[
          { label: "Contacts", href: "/contacts" },
          { label: entity?.canonical_name ?? entityId ?? "Entity" },
        ]}
      />

      {/* Content */}
      {isLoading && (
        <div className="space-y-4">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      )}

      {error && (
        <div className="text-destructive py-12 text-center text-sm">
          {(error as Error & { status?: number }).status === 404
            ? "Entity not found."
            : `Failed to load entity. ${(error as Error).message}`}
        </div>
      )}

      {entity && <EntityDetailView entity={entity} />}
    </div>
  );
}
