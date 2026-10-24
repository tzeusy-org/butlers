import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Query, type QueryProps } from "@/components/ui/query-boundary";
import { Tile, TileContent, TileDescription, TileHeader, TileTitle } from "@/components/ui/Tile";

interface QueryTileProps<T, E> extends Omit<QueryProps<T, E>, "errorFallback" | "unavailableFallback"> {
  title: ReactNode;
  description: ReactNode;
  errorMessage: string;
  errorTestId?: string;
}

function TileError({ title, description, message, testId, onRetry }: {
  title: ReactNode;
  description: ReactNode;
  message: string;
  testId?: string;
  onRetry: () => void;
}) {
  return (
    <Tile degraded>
      <TileHeader>
        <TileTitle>{title}</TileTitle>
        <TileDescription>{description}</TileDescription>
      </TileHeader>
      <TileContent role="alert" className="space-y-2">
        <p data-testid={testId} className="text-destructive text-sm">{message}</p>
        <Button variant="outline" size="sm"
          className="[@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:min-w-11"
          onClick={onRetry}>Retry</Button>
      </TileContent>
    </Tile>
  );
}

/** Shared query states around a tile's existing source-specific presentation. */
export function QueryTile<T, E = Error>({
  title, description, errorMessage, errorTestId, ...props
}: QueryTileProps<T, E>) {
  const retry = () => { void props.q.refetch(); };
  return (
    <Query {...props}
      errorFallback={<TileError title={title} description={description} message={errorMessage}
        testId={errorTestId} onRetry={retry} />}
      unavailableFallback={<TileError title={title} description={description}
        message={props.q.fetchStatus === "paused" ? "Offline. Data is unavailable." : "Data is unavailable."}
        testId={errorTestId} onRetry={retry} />}
    />
  );
}
