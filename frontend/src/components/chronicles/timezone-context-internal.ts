// ---------------------------------------------------------------------------
// ChroniclesTimezoneContext — shared context for timezone propagation (bu-k18cm)
//
// Internal module. Import this only from timezone-context.tsx (provider) and
// use-chronicles-timezone.ts (hook). Do not import directly in consumers.
// ---------------------------------------------------------------------------

import { createContext } from "react"
import { DEFAULT_TZ } from "./tz-format"

export const ChroniclesTimezoneContext = createContext<string>(DEFAULT_TZ)
