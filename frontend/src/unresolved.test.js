/**
 * Every identifier a component renders has to resolve to something.
 *
 * Twice now a rename has left a dangling reference that no build caught:
 * `IconFetched` after the icon set was regenerated, and `CodeBlock` after
 * `ui.jsx` renamed it to `Code`. Rollup compiles `<CodeBlock />` to
 * `jsx(CodeBlock)` and treats the free variable as a global, so the bundle
 * builds clean and the page throws `ReferenceError` in the browser — and only
 * on the code path that renders it, which is why the API reference page went
 * blank while every other page was fine.
 *
 * Rendering each page under test would not catch it either: the reference sat
 * behind a `status === 'ready'` branch. So this reads the source instead and
 * resolves names the way the browser will.
 */
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)))

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) return walk(full)
    // Test files never reach a browser, and they legitimately build
    // component tables the browser would never see.
    return /\.jsx?$/.test(name) && !/\.test\.jsx?$/.test(name) ? [full] : []
  })
}

/** Names a module brings into scope: imports, declarations, and destructuring. */
function declared(src) {
  const names = new Set()
  for (const m of src.matchAll(/import\s*\{([^}]*)\}\s*from/gs)) {
    for (const part of m[1].split(',')) {
      const name = part.trim().split(/\s+as\s+/).pop()
      if (name) names.add(name)
    }
  }
  for (const m of src.matchAll(/^import\s+([A-Za-z_$][\w$]*)\s*(?:,|from)/gm)) names.add(m[1])
  for (const m of src.matchAll(/(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)/g)) names.add(m[1])
  // `{ icon: Icon }` and `{ component: Page }` — renamed on the way in.
  for (const m of src.matchAll(/[\w$]+\s*:\s*([A-Z][\w$]*)/g)) names.add(m[1])
  // `const [name, Page] of ...` — bound by array destructuring.
  for (const m of src.matchAll(/(?:const|let|var)\s*\[([^\]]*)\]/g)) {
    for (const part of m[1].split(',')) {
      const name = part.trim()
      if (/^[A-Za-z_$][\w$]*$/.test(name)) names.add(name)
    }
  }
  return names
}

// Components the runtime provides rather than the module.
const GLOBALS = new Set(['Fragment'])

describe('every rendered component resolves', () => {
  const files = walk(SRC)

  it('finds the source tree', () => {
    expect(files.length).toBeGreaterThan(5)
  })

  for (const file of files) {
    const rel = file.slice(SRC.length + 1).split(sep).join('/')
    it(`${rel} has no dangling component reference`, () => {
      const src = readFileSync(file, 'utf8')
      const scope = declared(src)
      const used = new Set([...src.matchAll(/<([A-Z][\w$]*)/g)].map((m) => m[1]))
      const dangling = [...used].filter((n) => !scope.has(n) && !GLOBALS.has(n))
      expect(dangling, `${rel} renders ${dangling.join(', ')} but never imports or defines it`)
        .toEqual([])
    })
  }
})
