import { useEffect, useState } from 'react'

type Theme = 'light' | 'dark' | 'system'

const hasWindow = typeof window !== 'undefined'

function getSystemTheme(): 'light' | 'dark' {
  if (!hasWindow) return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function getStoredTheme(): Theme {
  if (!hasWindow) return 'system'
  try {
    const stored = window.localStorage.getItem('theme')
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  } catch {
    // localStorage may throw in private mode / sandboxed contexts.
  }
  return 'system'
}

export function useDarkMode() {
  const [theme, setTheme] = useState<Theme>(getStoredTheme)

  const resolvedTheme = theme === 'system' ? getSystemTheme() : theme

  useEffect(() => {
    if (!hasWindow) return
    const root = document.documentElement
    if (resolvedTheme === 'dark') {
      root.classList.add('dark')
    } else {
      root.classList.remove('dark')
    }
    try {
      window.localStorage.setItem('theme', theme)
    } catch {
      // ignore
    }
  }, [theme, resolvedTheme])

  // Listen for system preference changes when in system mode
  useEffect(() => {
    if (!hasWindow) return
    if (theme !== 'system') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = () => setTheme('system') // re-trigger effect
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [theme])

  return { theme, setTheme, resolvedTheme }
}
