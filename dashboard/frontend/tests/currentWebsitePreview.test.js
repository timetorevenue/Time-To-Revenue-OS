import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { JSDOM } from 'jsdom'
import { currentPreview } from '../src/currentWebsitePreview.js'

test('published-page draft renders literal text, keeps live structure, and blocks active content', { skip: "Needs the originating company's real website copy (not distributed) or fails identically in the originating tree; see tests/core_known_failures.json" }, () => {
  const browser = new JSDOM('', { url: 'http://127.0.0.1:4390' })
  globalThis.DOMParser = browser.window.DOMParser
  globalThis.location = browser.window.location
  const capture = JSON.parse(fs.readFileSync(new URL('../../../context/current_site/home.json', import.meta.url)))
  const data = { sections: capture.sections.map(s => ({ id: s.id, source: s.id, texts: {}, images: {}, styles: {} })) }
  const input = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
  data.sections[0].texts['s0:t1'] = input
  const result = new JSDOM(currentPreview(capture, data)).window.document
  assert.equal(result.querySelector('h1').textContent, input)
  assert.equal(result.querySelectorAll('script,[onerror],iframe,object,embed').length, 0)
  assert.equal(result.querySelectorAll('main > section').length, 14)
  assert.match(result.querySelector('meta[http-equiv]').content, /form-action 'none'/)
  assert.match(result.querySelector('meta[http-equiv]').content, /default-src 'none'/)
  assert.equal(result.querySelector('link').getAttribute('href'), 'http://127.0.0.1:4390/studio/current-site.css')
  assert.equal(capture.sections[0].texts[1].text, 'AI operations and workflow systems for Example City service businesses.')
  delete globalThis.DOMParser
  delete globalThis.location
})
