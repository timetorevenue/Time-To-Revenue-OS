import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'

const run = env => spawnSync(process.execPath, ['--input-type=module', '-e',
  "import('./src/studio/site-config.js').then(m => console.log(JSON.stringify([m.SITE_ID, m.SITE_DOMAIN, m.SITE_URL])))"],
  { env: { PATH: process.env.PATH, ...env }, encoding: 'utf8' })

test('site id and domain come from the environment, defaults unchanged', () => {
  assert.deepEqual(JSON.parse(run({}).stdout), ['default-site', 'core-site.example', 'https://www.core-site.example'])
  assert.deepEqual(JSON.parse(run({ TTROS_STUDIO_SITE_ID: 'acme-site', TTROS_SITE_DOMAIN: 'acme.example.org' }).stdout),
    ['acme-site', 'acme.example.org', 'https://www.acme.example.org'])
})
