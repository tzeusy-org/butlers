import type { EntityActivityItem, EntityActivityResponse } from "@/api/types";

export function entityActivityItemKey(item: EntityActivityItem): string {
  return `${item.src}:${item.store ?? "none"}:${item.id}`;
}

/** Detect an offset-pagination snapshot that moved between page reads. */
export function entityActivityPagesDrifted(
  pages: readonly EntityActivityResponse[],
): boolean {
  if (pages.length < 2) return false;
  const expectedTotal = pages[0].total;
  const seen = new Set<string>();
  for (const [pageIndex, page] of pages.entries()) {
    if (page.total !== expectedTotal) return true;
    for (const item of page.items) {
      const key = entityActivityItemKey(item);
      if (pageIndex > 0 && seen.has(key)) return true;
      seen.add(key);
    }
  }
  return false;
}

/** Refuse to present a short, terminal unique set as a complete stream. */
export function entityActivityNeedsRefresh(
  pages: readonly EntityActivityResponse[],
  hasNextPage: boolean,
  uniqueCount: number,
): boolean {
  if (pages.length === 0) return false;
  return (
    entityActivityPagesDrifted(pages) ||
    (!hasNextPage && uniqueCount < pages[0].total)
  );
}
