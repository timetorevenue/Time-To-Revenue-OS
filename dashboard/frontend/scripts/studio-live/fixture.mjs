// Disposable Next.js fixtures of the pinned website commit (Git objects only; the registered
// checkout's working tree is never read or written). Builds run with integrations disabled and
// no inherited credentials; servers bind loopback only.
import fs from 'node:fs'
import path from 'node:path'
import net from 'node:net'
import {execFileSync, spawn} from 'node:child_process'

export const FIXTURE_ENTRIES = ['app', 'components', 'content', 'lib', 'public', 'package.json', 'package-lock.json', 'tsconfig.json',
 'next-env.d.ts', 'next.config.ts', 'postcss.config.mjs', 'tailwind.config.ts', 'eslint.config.mjs', '.eslintrc.json']
export const BUILD_ENV = {NEXT_TELEMETRY_DISABLED: '1', GUIDE_CAPTURE_ENABLED: 'false', NEWSLETTER_CAPTURE_ENABLED: 'false',
 NEWSLETTER_RELAYS_ENABLED: 'false', TTROS_SUBSCRIBERS_ENABLED: 'false'}

export function cleanEnv(folder, cache) {
 fs.writeFileSync(path.join(folder, '.npm-user.config'), '')
 fs.writeFileSync(path.join(folder, '.npm-global.config'), '')
 return {PATH: process.env.PATH, HOME: folder, npm_config_cache: cache || path.join(folder, '.npm-cache'),
  npm_config_userconfig: path.join(folder, '.npm-user.config'), npm_config_globalconfig: path.join(folder, '.npm-global.config'),
  npm_config_update_notifier: 'false', ...BUILD_ENV}
}

export function archive(repo, commit, folder) {
 fs.mkdirSync(folder, {recursive: true})
 const top = execFileSync('git', ['-C', repo, 'ls-tree', '--name-only', commit], {encoding: 'utf8'}).trim().split('\n')
 const entries = top.filter(p => FIXTURE_ENTRIES.includes(p))
 const tar = path.join(folder, '.source.tar')
 execFileSync('git', ['-C', repo, 'archive', '--format=tar', '--output', tar, commit, ...entries])
 execFileSync('tar', ['-x', '-C', folder, '-f', tar])
 fs.rmSync(tar)
 return entries
}

export function install(folder, env, log) {
 execFileSync('npm', ['ci', '--include=dev', '--no-audit', '--no-fund', '--prefer-offline'], {cwd: folder, env, stdio: ['ignore', log, log], timeout: 600000})
}

export function build(folder, env, log) {
 execFileSync(process.execPath, [path.join(folder, 'node_modules/next/dist/bin/next'), 'build'], {cwd: folder, env, stdio: ['ignore', log, log], timeout: 900000})
 return fs.readFileSync(path.join(folder, '.next/BUILD_ID'), 'utf8').trim()
}

export async function freePort() {
 return new Promise((resolve, reject) => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const {port} = s.address(); s.close(() => resolve(port)) }); s.on('error', reject) })
}

export async function serve(folder, env, log) {
 const port = await freePort()
 const child = spawn(process.execPath, [path.join(folder, 'node_modules/next/dist/bin/next'), 'start', '--hostname', '127.0.0.1', '--port', String(port)], {cwd: folder, env, stdio: ['ignore', log, log]})
 const url = `http://127.0.0.1:${port}`
 for (let i = 0; i < 200; i++) {
  if (child.exitCode !== null) throw Error('next start exited')
  try { const r = await fetch(url + '/robots.txt'); if (r.status) return {url, port, stop: () => child.kill('SIGTERM'), child} } catch { /* starting */ }
  await new Promise(r => setTimeout(r, 150))
 }
 child.kill('SIGTERM')
 throw Error('next start did not become ready')
}

export function routes(folder) {
 const prerender = JSON.parse(fs.readFileSync(path.join(folder, '.next/prerender-manifest.json'), 'utf8'))
 const app = JSON.parse(fs.readFileSync(path.join(folder, '.next/server/app-paths-manifest.json'), 'utf8'))
 return {prerendered: Object.keys(prerender.routes || {}).sort(), app: Object.keys(app).sort()}
}
