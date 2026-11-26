// Vite dependency-cache isolation. Only the production Dashboard server (aos-frontend:
// `vite --port 3010 --strictPort` on this checkout's real node_modules) uses the default
// node_modules/.vite. Every other server — another port, a worktree whose node_modules is a
// symlink, `npm test`'s embedded servers, a build — gets its own cache keyed by port and checkout,
// so re-optimizing dependencies can never rewrite the files the running Dashboard serves.
// Revisit: when the aos-frontend command or port changes. Last touched: 2026-11-18.
import { createHash } from 'node:crypto'
import { lstatSync, realpathSync } from 'node:fs'
import path from 'node:path'

export const PRODUCTION_PORT = 3010
export const PRODUCTION_CACHE = '.vite'

export function argPort(argv) {
  let port
  argv.forEach((arg, i) => {
    if (arg === '--port') port = argv[i + 1]
    else if (arg.startsWith('--port=')) port = arg.slice('--port='.length)
  })
  const value = Number(port)
  return port === undefined || !Number.isInteger(value) ? undefined : value
}

function sharedModules(root) {
  try { return lstatSync(path.join(root, 'node_modules')).isSymbolicLink() } catch { return false }
}

export function viteCacheDir({ root, argv = process.argv, env = process.env }) {
  if (env.TTROS_VITE_CACHE_DIR) return path.resolve(root, env.TTROS_VITE_CACHE_DIR)
  const port = argPort(argv)
  const production = port === PRODUCTION_PORT && argv.includes('--strictPort') && !sharedModules(root)
  if (production) return path.join(root, 'node_modules', PRODUCTION_CACHE)
  let real = root
  try { real = realpathSync(root) } catch { /* keep the given root */ }
  const checkout = createHash('sha256').update(real).digest('hex').slice(0, 10)
  return path.join(root, 'node_modules', `.vite-dev-${port ?? 'embedded'}-${checkout}`)
}
