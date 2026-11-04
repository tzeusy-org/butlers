import { useId } from 'react'

/** A per-SVG "not listening" hatch pattern id that is safe inside `url(#...)`. */
export function useHatchPatternId(): string {
  return `not-listening-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
}
