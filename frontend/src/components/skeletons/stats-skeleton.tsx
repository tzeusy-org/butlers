import { Section, SectionContent, SectionHeader } from "@/components/ui/Section";
import { Skeleton } from "@/components/ui/skeleton";

interface StatsSkeletonProps {
  /** Number of stat cards to render. @default 4 */
  count?: number;
}

/**
 * Skeleton loader for a row of stats cards (e.g. notification stats bar).
 *
 * Each card shows a pulsing title placeholder and a larger value placeholder,
 * matching the layout of real stat cards.
 */
export function StatsSkeleton({ count = 4 }: StatsSkeletonProps) {
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
      {Array.from({ length: count }, (_, i) => (
        <Section key={i}>
          <SectionHeader className="flex flex-row items-center justify-between pb-2">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-4 w-4 rounded-full" />
          </SectionHeader>
          <SectionContent>
            <Skeleton className="h-8 w-16" />
          </SectionContent>
        </Section>
      ))}
    </div>
  );
}
