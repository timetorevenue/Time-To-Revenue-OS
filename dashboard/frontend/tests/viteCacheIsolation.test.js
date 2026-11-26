// Development servers, tests and worktrees must never share the production Dashboard's
// Vite dependency cache (2026-11-18: a worktree server rewrote it and blanked Studio on 3010).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, symlinkSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { resolveConfig } from 'vite'
import { viteCacheDir, argPort } from '../vite.cache.js'

const vite = '/srv/frontend/node_modules/.bin/vite'
const production = ['node', vite, '--host', '127.0.0.1', '--port', '3010', '--strictPort']

function checkout(shared) {
  const base = mkdtempSync(path.join(tmpdir(), 'vite-cache-'))
  const root = path.join(base, 'frontend')
  mkdirSync(root)
  if (shared) { mkdirSync(path.join(base, 'real_modules')); symlinkSync(path.join(base, 'real_modules'), path.join(root, 'node_modules')) }
  else mkdirSync(path.join(root, 'node_modules'))
  return { base, root }
}

test('port parsing follows the last --port, as Vite does', () => {
  assert.equal(argPort(['--port', '3010', '--port', '4318']), 4318)
  assert.equal(argPort(['--port=4328']), 4328)
  assert.equal(argPort(['build']), undefined)
})

test('only the production 3010 strictPort server on real node_modules uses node_modules/.vite', t => {
  const { base, root } = checkout(false)
  t.after(() => rmSync(base, { recursive: true, force: true }))
  const prod = viteCacheDir({ root, argv: production, env: {} })
  assert.equal(prod, path.join(root, 'node_modules', '.vite'))
  const others = [
    ['node', vite, '--host', '127.0.0.1', '--port', '3010', '--port', '4318'],        // npm run dev -- --port 4318
    ['node', vite, '--host', '127.0.0.1', '--port', '4328', '--strictPort'],          // npx vite --port 4328 --strictPort
    ['node', vite, '--host', '127.0.0.1', '--port', '3010'],                          // plain npm run dev (no strictPort)
    ['node', '--test', 'tests/x.test.js'],                                            // embedded test servers
    ['node', vite, 'build'],
  ].map(argv => viteCacheDir({ root, argv, env: {} }))
  for (const dir of others) {
    assert.notEqual(dir, prod)
    assert.match(path.basename(dir), /^\.vite-dev-/)
  }
  assert.equal(new Set(others.slice(0, 2)).size, 2, 'different ports never share a cache')
})

test('a worktree with symlinked node_modules is isolated even on the production command line', t => {
  const main = checkout(false), worktree = checkout(true)
  t.after(() => { rmSync(main.base, { recursive: true, force: true }); rmSync(worktree.base, { recursive: true, force: true }) })
  const dir = viteCacheDir({ root: worktree.root, argv: production, env: {} })
  assert.match(path.basename(dir), /^\.vite-dev-3010-[0-9a-f]{10}$/)
  const other = viteCacheDir({ root: main.root, argv: ['node', vite, '--port', '3010'], env: {} })
  assert.notEqual(path.basename(dir), path.basename(other), 'cache key includes the checkout')
})

test('explicit override is honoured', () => {
  assert.equal(viteCacheDir({ root: '/srv/frontend', argv: production, env: { TTROS_VITE_CACHE_DIR: '/tmp/x-cache' } }), '/tmp/x-cache')
})

test('the real config gives this test process an isolated cache, never the production one', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url))
  const config = await resolveConfig({ root, logLevel: 'silent' }, 'serve')
  assert.notEqual(path.resolve(config.cacheDir), path.resolve(root, 'node_modules', '.vite'))
  assert.match(path.basename(config.cacheDir), /^\.vite-dev-embedded-/)
  assert.equal(config.server.port, 3010)
  assert.deepEqual(Object.keys(config.server.proxy), ['/api/website/studio', '/api'])
  assert.deepEqual(config.server.allowedHosts, ['node.tailnet.example'])
})
