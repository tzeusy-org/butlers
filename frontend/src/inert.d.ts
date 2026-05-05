// React 18's @types/react does not include the `inert` HTML attribute.
// `inert` blocks interaction AND keyboard focus on an element and all its
// descendants — unlike `aria-hidden`, which removes elements from the
// accessibility tree but leaves them tabbable.
//
// This augmentation adds `inert` to React's HTMLAttributes so JSX accepts it
// without a type cast. The attribute must be set to `""` (present) or
// `undefined` (absent); React does not support boolean HTML attributes for
// non-React-defined props in this version.
import 'react'

declare module 'react' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface HTMLAttributes<T> {
    inert?: '' | undefined
  }
}
