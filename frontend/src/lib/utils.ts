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
