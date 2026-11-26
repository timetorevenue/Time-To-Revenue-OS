// Builds the UNTOUCHED pinned website commit (no Studio adapter, no draft) in a fresh directory, as
// the visual/HTML baseline for Studio previews. Read-only for the source: git archive of committed
// objects; isolated npm cache; telemetry and capture/sending flags off. Never publishes or serves
// beyond loopback. Usage: node scripts/studio-baseline-build.mjs <repository> <output-dir>
// Revisit when studio-site-fixture.mjs changes which source entries it archives. Last touched: 2026-11-21.
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import manifest from '../src/studio/source-manifest.json' with { type: 'json' }
const [repository, output] = process.argv.slice(2)
if (!repository || !output) throw Error('Usage: node scripts/studio-baseline-build.mjs <repository> <output-dir>')
const folder = path.resolve(output, 'site')
fs.mkdirSync(folder, { recursive: true })
const keep = ['app', 'components', 'content', 'lib', 'public', 'package.json', 'package-lock.json', 'tsconfig.json', 'next-env.d.ts', 'next.config.ts', 'postcss.config.mjs', 'tailwind.config.ts', 'eslint.config.mjs', '.eslintrc.json']
const entries = execFileSync('git', ['-C', repository, 'ls-tree', '--name-only', manifest.commit], { encoding: 'utf8' }).trim().split('\n').filter(p => keep.includes(p))
execFileSync('git', ['-C', repository, 'archive', '--format=tar', '--output', path.join(output, 'source.tar'), manifest.commit, ...entries])
execFileSync('tar', ['-x', '-C', folder, '-f', path.join(output, 'source.tar')])
for (const name of ['npm-user.config', 'npm-global.config']) fs.writeFileSync(path.join(output, name), '')
const env = { PATH: process.env.PATH, npm_config_cache: path.join(output, 'npm-cache'), npm_config_userconfig: path.join(output, 'npm-user.config'), npm_config_globalconfig: path.join(output, 'npm-global.config'),
  NEXT_TELEMETRY_DISABLED: '1', GUIDE_CAPTURE_ENABLED: 'false', NEWSLETTER_CAPTURE_ENABLED: 'false', NEWSLETTER_RELAYS_ENABLED: 'false', TTROS_SUBSCRIBERS_ENABLED: 'false' }
execFileSync('npm', ['ci', '--include=dev', '--no-audit', '--no-fund', '--prefer-offline'], { cwd: folder, env, stdio: ['ignore', 2, 2], timeout: 600000 })
execFileSync('npm', ['run', 'build'], { cwd: folder, env, stdio: ['ignore', 2, 2], timeout: 900000 })
const result = { commit: manifest.commit, folder, nextBuildId: fs.readFileSync(path.join(folder, '.next/BUILD_ID'), 'utf8').trim(), routes: Object.keys(JSON.parse(fs.readFileSync(path.join(folder, '.next/server/app-paths-manifest.json'), 'utf8'))), studioAdapter: false, modelCalls: 0 }
fs.writeFileSync(path.join(output, 'baseline.json'), JSON.stringify(result, null, 2))
console.log(JSON.stringify(result, null, 2))
