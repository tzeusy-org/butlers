import { Link, useParams } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useEntity } from "@/hooks/use-memory";

export default function EntityDetailPage() {
  const { entityId } = useParams<{ entityId: string }>();
  const { data, isLoading, error } = useEntity(entityId);
  const entity = data?.data;

  return (
    <div className="space-y-6">
      {/* Breadcrumbs */}
      <Breadcrumbs
        items={[
          { label: "Entities", href: "/entities" },
          { label: entity?.canonical_name ?? entityId ?? "Entity" },
        ]}
      />

      {/* Loading */}
      {isLoading && (
        <div className="space-y-4">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="text-destructive py-12 text-center text-sm">
          Failed to load entity. {(error as Error).message}
        </div>
      )}

      {/* Content */}
      {entity && (
        <>
          {/* Header card */}
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center gap-3">
                <CardTitle className="text-2xl">
                  {entity.canonical_name}
                </CardTitle>
                <Badge>{entity.entity_type}</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {/* Aliases */}
              {entity.aliases.length > 0 && (
                <div>
                  <p className="text-muted-foreground mb-1 text-sm font-medium">
                    Aliases
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {entity.aliases.map((alias) => (
                      <Badge key={alias} variant="secondary">
                        {alias}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Metadata */}
              {Object.keys(entity.metadata).length > 0 && (
                <div>
                  <p className="text-muted-foreground mb-1 text-sm font-medium">
                    Metadata
                  </p>
                  <pre className="rounded-md bg-muted p-3 text-xs overflow-x-auto">
                    {JSON.stringify(entity.metadata, null, 2)}
                  </pre>
                </div>
              )}

              {/* Timestamps */}
              <div className="flex gap-6 text-xs text-muted-foreground">
                <span>
                  Created: {new Date(entity.created_at).toLocaleString()}
                </span>
                <span>
                  Updated: {new Date(entity.updated_at).toLocaleString()}
                </span>
              </div>
            </CardContent>
          </Card>

          {/* Facts tab */}
          <Card>
            <CardHeader>
              <CardTitle>
                Facts ({entity.fact_count})
              </CardTitle>
            </CardHeader>
            <CardContent>
              {entity.recent_facts.length === 0 ? (
                <p className="text-muted-foreground py-4 text-center text-sm">
                  No facts linked to this entity.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left text-muted-foreground">
                        <th className="pb-2 pr-4 font-medium">Subject</th>
                        <th className="pb-2 pr-4 font-medium">Predicate</th>
                        <th className="pb-2 pr-4 font-medium">Content</th>
                        <th className="pb-2 pr-4 font-medium text-right">
                          Confidence
                        </th>
                        <th className="pb-2 font-medium">Created</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entity.recent_facts.map((fact) => (
                        <tr
                          key={fact.id}
                          className="border-b last:border-0 hover:bg-muted/50"
                        >
                          <td className="py-2 pr-4 font-medium">
                            {fact.subject}
                          </td>
                          <td className="py-2 pr-4 text-muted-foreground">
                            {fact.predicate}
                          </td>
                          <td className="py-2 pr-4 max-w-md truncate">
                            {fact.content}
                          </td>
                          <td className="py-2 pr-4 text-right tabular-nums">
                            {(fact.confidence * 100).toFixed(0)}%
                          </td>
                          <td className="py-2 text-muted-foreground">
                            {new Date(fact.created_at).toLocaleDateString()}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Contact link */}
          <Card>
            <CardHeader>
              <CardTitle>Linked Contact</CardTitle>
            </CardHeader>
            <CardContent>
              {entity.linked_contact_id ? (
                <Link
                  to={`/contacts/${entity.linked_contact_id}`}
                  className="text-primary hover:underline"
                >
                  {entity.linked_contact_name ?? entity.linked_contact_id}
                </Link>
              ) : (
                <p className="text-muted-foreground text-sm">
                  No linked contact.
                </p>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
