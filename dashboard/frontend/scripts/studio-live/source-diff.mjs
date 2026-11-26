// The exact website source a Studio state generates, as a diff: element documents through the live
// generator, then the code layer (code.js). Same functions as build-live.mjs, without a build.
// Usage: node source-diff.mjs <request.json>
// request: {repository, commit, snapshot, after: {docs, code}, before?: {docs, code}, output}
// output: {files: [{path, status, diff}], changed: [paths]}; `before` defaults to the pinned source.
// Zero models, no network, nothing written outside `output`.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {execFileSync} from 'node:child_process'
import {readCommit} from './git-source.mjs'
import {generate} from './live-source.mjs'
import {applyCodeLayer} from '../../src/studio/live/code.js'
import {applySitePages} from './site-pages.mjs'

const request = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const pristine = JSON.parse(fs.readFileSync(path.join(request.snapshot, 'pristine.json'), 'utf8'))
const model = JSON.parse(fs.readFileSync(path.join(request.snapshot, 'model.json'), 'utf8'))
if (pristine.commit !== request.commit) throw Error('Snapshot and requested website commit disagree.')
const source = readCommit(request.repository, request.commit)
const docFiles = new Set(Object.keys(model.docs))
const assetPath = ref => `/studio-assets/${ref.slice(6)}`
const imported = Object.fromEntries(Object.entries(model.docs).map(([id, d]) => [id, d.tree]))
const render = state => {
 if (!state) return {}
 const pages = state.code?.pages || {}
 // Created documents only exist while their page does (a removed page leaves no file).
 const docs = Object.fromEntries(Object.entries({...imported, ...(state.docs || {})}).filter(([id]) => model.docs[id] || pages[id]))
 return applySitePages(pages, source.files, applyCodeLayer(state.code || {}, source.files, generate(pristine, source.files, docs, {assetPath, pages}), {docFiles, assetPath}), docs, {assetPath})
}
const after = render(request.after)
const before = render(request.before)
const text = (out, file) => out[file] ?? source.files.get(file) ?? null
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-diff-'))
const files = []
try {
 for (const file of [...new Set([...Object.keys(after), ...Object.keys(before)])].sort()) {
  const a = text(before, file), b = text(after, file)
  if (a === b) continue
  const fa = path.join(tmp, 'a'), fb = path.join(tmp, 'b')
  fs.writeFileSync(fa, a ?? ''); fs.writeFileSync(fb, b ?? '')
  let diff
  try { diff = execFileSync('git', ['diff', '--no-index', '-U3', '--', fa, fb], {encoding: 'utf8', maxBuffer: 64 << 20}) } catch (e) { if (e.status !== 1) throw e; diff = e.stdout }
  diff = diff.replace(/^diff --git .*\n/m, '').replace(/^--- .*$/m, `--- a/${file}`).replace(/^\+\+\+ .*$/m, `+++ b/${file}`)
  files.push({path: file, status: a === null ? 'added' : b === null ? 'removed' : 'modified', diff})
 }
} finally { fs.rmSync(tmp, {recursive: true, force: true}) }
fs.writeFileSync(request.output, JSON.stringify({files, changed: files.map(f => f.path)}))
