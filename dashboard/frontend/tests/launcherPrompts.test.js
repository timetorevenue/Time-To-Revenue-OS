import assert from 'node:assert/strict'
import test from 'node:test'

import { CODEX_LINUX_LAUNCH, SCOPED_PERMISSION_HEADER, launcherPrompt } from '../src/launcherPrompts.js'

test('Codex copy prompt uses the native workbench and current runtime policy', () => {
  assert.equal(launcherPrompt('codex'), `${SCOPED_PERMISSION_HEADER}\n\n${CODEX_LINUX_LAUNCH}`)
  assert.match(CODEX_LINUX_LAUNCH, /cd "\$\{AOS_ROOT:-\/opt\/ttros-core\}"/)
  assert.match(CODEX_LINUX_LAUNCH, /command -v codex\ncodex --version\ncodex '<TASK>'$/)
  assert.doesNotMatch(CODEX_LINUX_LAUNCH, /aos-codex|\/api\/wsl|--dangerously|--yolo|\/mnt\/c|\bresume\b|--last/)
  assert.match(SCOPED_PERMISSION_HEADER, /rules\/runtime_permission_policy\.md/)
  assert.match(SCOPED_PERMISSION_HEADER, /Never push/)
})

test('Claude copy prompt retains the exact same permission header and Linux-only root', () => {
  const prompt = launcherPrompt('claude-code')
  assert.ok(prompt.startsWith(`${SCOPED_PERMISSION_HEADER}\n\n`))
  assert.match(prompt, /cd "\$\{AOS_ROOT:-\/opt\/ttros-core\}"/)
  assert.doesNotMatch(prompt, /\/mnt\/c|Windows Start|C:\\/)
})
