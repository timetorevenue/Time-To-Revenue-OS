// Revisit: when David's attachment contract changes. · Created 2026-09-13.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { replyArtifactLinks } from '../src/askDavidState.js'

const read = path => fs.readFileSync(new URL(path, import.meta.url), 'utf8')
const source = read('../src/components/AskDavid.jsx')

test('David has a real paperclip wired to the shared upload mechanism', () => {
  assert.match(source, /import \{ askDavid, uploadFile, pollDavidJob \} from '\.\.\/api'/)
  assert.match(source, /data-testid="ask-david-attach-button"/)
  assert.match(source, /data-testid="ask-david-file-input"/)
  assert.match(source, /await uploadFile\(file, \{ david: true \}\)/)
})

test('an attachment is sent as an opaque upload_id reference, never a raw filesystem path', () => {
  assert.match(source, /attachments\.filter\(item => item\.upload_id\)\.map\(item => `upload:\$\{item\.upload_id\}`\)/)
  assert.match(source, /askDavid\(clean \|\| "Examine the attached files\.", attachmentRefs(?:,|\))/)
})

test('sent attachments stay visibly associated with the message that carried them', () => {
  assert.match(source, /data-testid="ask-david-sent-attachments"/)
  assert.match(source, /entry\.attachments\?\.length > 0/)
})

test('a response that ingests into the Business Brain is rendered distinctly from an ordinary reply', () => {
  assert.match(source, /entry\.kind === 'memory_intake_ingested'/)
  assert.match(source, /data-testid="ask-david-ingested"/)
})

test('attaching a file alone never calls a memory-intake ingest endpoint from the frontend', () => {
  assert.doesNotMatch(source, /ingestMemoryIntake/)
})

test('render generated downloads only from valid capability receipts', () => {
  const id = 'a'.repeat(32)
  const href = `/api/david/artifacts/${id}?conversation_id=dashboard-example`
  const result = { id, filename: 'summary.html', dashboard_download: href }
  const receipt = { action: 'execute', capability: 'files.write', result }
  const capture = { action: 'execute', capability: 'browser.session', result: { artifacts: [result] } }
  assert.deepEqual(replyArtifactLinks({ capability_receipts: [capture] }), [{ id, filename: 'summary.html', href }])
  assert.deepEqual(replyArtifactLinks({ capability_receipts: [receipt, receipt] }), [
    { id, filename: 'summary.html', href },
  ])
  assert.deepEqual(replyArtifactLinks({ capability_receipts: [
    { ...receipt, result: { ...result, dashboard_download: `https://example.com${href}` } },
    { ...receipt, result: { ...result, dashboard_download: href + '&extra=true' } },
    { ...receipt, capability: 'web.retrieve' },
  ] }), [])
  assert.match(source, /href=\{file\.href\} download=\{file\.filename\}/)
})
