import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Capitalizes the first letter of each word in a string.
 * Splits on whitespace and hyphens, capitalizes each segment, joins with a space.
 *
 * @example titleize("general") // "General"
 * @example titleize("chronicler") // "Chronicler"
 */
export function titleize(str: string): string {
  return str
    .split(/[\s-]+/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ")
}

/**
 * Compose two optional event handlers into one that calls both, in order.
 * Used where a component needs to attach its own handler (e.g.
 * usePrefetchOnIntent's onPointerEnter) without clobbering a caller-supplied
 * one for the same DOM event.
 */
export function composeHandlers<E>(
  a: ((event: E) => void) | undefined,
  b: ((event: E) => void) | undefined,
): ((event: E) => void) | undefined {
  if (!a) return b
  if (!b) return a
  return (event: E) => {
    a(event)
    b(event)
  }
}
