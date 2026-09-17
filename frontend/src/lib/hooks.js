import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { loadAll } from './client'

/**
 * Hash routing.
 *
 * GitHub Pages has no rewrite rules, so a path-based route would 404 on a hard
 * refresh. The fragment never reaches the server, which makes it the routing
 * scheme a static host actually supports.
 */
export function useRoute(fallback = 'overview') {
  const read = useCallback(() => {
    const raw = window.location.hash.replace(/^#\/?/, '').split('?')[0]
    return raw || fallback
  }, [fallback])

  const [route, setRoute] = useState(read)

  useEffect(() => {
    const onChange = () => setRoute(read())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [read])

  const navigate = useCallback((next) => {
    window.location.hash = `#/${next}`
    window.scrollTo({ top: 0, behavior: 'instant' in window ? 'instant' : 'auto' })
  }, [])

  return [route, navigate]
}

export function useTheme() {
  const [theme, setTheme] = useState(
    () => document.documentElement.dataset.theme || 'light'
  )

  const toggle = useCallback(() => {
    setTheme((current) => {
      const next = current === 'dark' ? 'light' : 'dark'
      document.documentElement.dataset.theme = next
      try {
        localStorage.setItem('pkf.theme', next)
      } catch {
        // Private browsing. The theme still applies; it just will not persist.
      }
      return next
    })
  }, [])

  return [theme, toggle]
}

/**
 * Load one or more named resources.
 *
 * Returns a discriminated state rather than three loose booleans, so a render
 * cannot show a spinner and an error at the same time.
 */
export function useData(names, nonce = 0) {
  const key = names.join(',')
  const [state, setState] = useState({ status: 'loading' })
  const latest = useRef(0)

  useEffect(() => {
    const ticket = ++latest.current
    setState({ status: 'loading' })

    // `nonce` changes after the refresh button has emptied the cache, so a
    // plain load here already re-fetches; asking for `fresh` as well would
    // bypass the cache on every navigation.
    loadAll(key.split(','))
      .then((bodies) => {
        if (latest.current === ticket) setState({ status: 'ready', bodies })
      })
      .catch((error) => {
        if (latest.current === ticket) setState({ status: 'error', error })
      })
  }, [key, nonce])

  return state
}

/** Debounced value — keeps a 1,000-row filter off the keystroke path. */
export function useDebounced(value, delay = 180) {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return settled
}

/** Sort state plus the comparator that applies it. */
export function useSort(initialKey, initialAscending = false) {
  const [sort, setSort] = useState({ key: initialKey, ascending: initialAscending })

  const toggle = useCallback((key) => {
    setSort((current) =>
      current.key === key
        ? { key, ascending: !current.ascending }
        : { key, ascending: false }
    )
  }, [])

  const apply = useCallback(
    (rows, accessors = {}) => {
      const get = accessors[sort.key] || ((row) => row[sort.key])
      // A missing value is not a small value: nulls sort last in both
      // directions rather than piling up at whichever end is "low".
      const present = []
      const missing = []
      for (const row of rows) {
        const value = get(row)
        ;(value === null || value === undefined || value === '' ? missing : present).push(row)
      }
      present.sort((a, b) => {
        const left = get(a)
        const right = get(b)
        const compared =
          typeof left === 'string' || typeof right === 'string'
            ? String(left).localeCompare(String(right), 'en')
            : left - right
        return sort.ascending ? compared : -compared
      })
      return present.concat(missing)
    },
    [sort]
  )

  return { sort, toggle, apply, set: setSort }
}

/** Client-side pagination that resets whenever the underlying list changes. */
export function usePage(rows, size = 50) {
  const [page, setPage] = useState(0)
  const pages = Math.max(1, Math.ceil(rows.length / size))

  useEffect(() => {
    setPage(0)
  }, [rows])

  const slice = useMemo(() => rows.slice(page * size, page * size + size), [rows, page, size])

  return {
    page,
    pages,
    slice,
    from: rows.length ? page * size + 1 : 0,
    to: Math.min(rows.length, (page + 1) * size),
    total: rows.length,
    next: () => setPage((p) => Math.min(p + 1, pages - 1)),
    previous: () => setPage((p) => Math.max(p - 1, 0)),
  }
}
