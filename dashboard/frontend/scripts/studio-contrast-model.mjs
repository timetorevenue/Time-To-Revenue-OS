// Writes src/studio/contrast-model.json from homepage.json, services.json and contrast-rules.json.
// Deterministic; the frontend test fails if the committed model is stale. Usage: node scripts/studio-contrast-model.mjs [--check]
import fs from 'node:fs'
import { buildContrastModel } from '../src/studio/contrast-model.js'
const read = p => JSON.parse(fs.readFileSync(new URL(p, import.meta.url), 'utf8'))
const target = new URL('../src/studio/contrast-model.json', import.meta.url)
// Stage 3: homepage sections, then every service page's sections (services.json).
const sections = [...read('../src/studio/homepage.json').sections, ...Object.values(read('../src/studio/services.json').pages).flatMap(p => p.sections)]
const text = JSON.stringify(buildContrastModel(sections, read('../src/studio/contrast-rules.json')), null, 1) + '\n'
if (process.argv.includes('--check')) {
  if (fs.readFileSync(target, 'utf8') !== text) { console.error('contrast-model.json is stale; run node scripts/studio-contrast-model.mjs'); process.exit(1) }
  console.log('contrast-model.json is current')
} else { fs.writeFileSync(target, text); console.log(`wrote ${target.pathname}`) }
