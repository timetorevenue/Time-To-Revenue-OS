// Explicit, read-only import of the three real service pages (Stage 3). Renders each route's
// actual page module (app/services/<slug>/page.tsx → components/ServicePage.tsx with
// content/services.ts) to static markup, exactly like snapshot-homepage.mjs, and records:
//  - each page's sections as Studio section TYPES (text nodes → studio-text placeholders),
//  - the page's original metadata and JSON-LD (canonical URL, title, description, graph),
//  - where the content schema's SEO-bearing fields (name, description) live in those sections.
// Never runs Next and never writes into the website checkout. Writes src/studio/services.json and
// src/studio/service-manifest.json (hashes of every source file the service adapter depends on).
// Usage (from dashboard/frontend): node scripts/snapshot-services.mjs [/path/to/default-site]
// Revisit when the website's ServicePage, services content or routes change. Last touched: 2026-11-18.
import { build } from 'esbuild'
import { JSDOM } from 'jsdom'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import { execFileSync } from 'node:child_process'
import pages from '../src/studio/pages.json' with { type: 'json' }
import { snapshotSource } from './studio-source-archive.mjs'
const require = createRequire(import.meta.url)
// Usage: node scripts/snapshot-services.mjs [repository] [--commit <sha>] (--commit reads a git archive)
const { repository, root: source, commit } = snapshotSource(process.argv.slice(2))
const out = path.resolve('src/studio')

// Draft-only Studio pages (test-offer) have no route in the website: they render the real
// ServicePage of their content's route and take their own route/canonical URL.
const allServices = Object.entries(pages.pages).filter(([, p]) => p.template === 'service')
const services = allServices.filter(([, p]) => !p.draftOnly)
const entry = `import React from 'react'; import {renderToStaticMarkup} from 'react-dom/server';
import { services } from '${source}/content/services.ts';
import Home, { metadata as homeMeta } from '${source}/app/page.tsx';
${services.map(([, p], i) => `import Page${i}, { metadata as meta${i} } from '${source}/app${p.route}/page.tsx';`).join('\n')}
export const rendered = { ${services.map(([id], i) => `'${id}': { html: renderToStaticMarkup(React.createElement(Page${i})), metadata: meta${i} }`).join(', ')} };
export const home = { html: renderToStaticMarkup(React.createElement(Home)), metadata: homeMeta };
export { services };`
const result = await build({ stdin: { contents: entry, resolveDir: process.cwd(), loader: 'tsx' }, bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic', plugins: [{ name: 'snapshot-shims', setup(b) {
  b.onResolve({ filter: /^react($|\/)|^react-dom\// }, a => ({ path: require.resolve(a.path), external: true }))
  b.onResolve({ filter: /^@\// }, a => ({ path: path.join(source, a.path.slice(2)) + (fs.existsSync(path.join(source, a.path.slice(2)) + '.tsx') ? '.tsx' : '.ts') }))
  b.onResolve({ filter: /^next\// }, a => ({ path: a.path, namespace: 'shim' }))
  b.onLoad({ filter: /.*/, namespace: 'shim' }, a => ({ loader: 'jsx', contents: a.path === 'next/navigation' ? `export const usePathname=()=>'/';` : `import React from 'react'; export default function Shim({children,fill,priority,sizes,unoptimized,...p}){return React.createElement('${a.path === 'next/image' ? 'img' : 'a'}',p,children)}` }))
} }] })
const module = { exports: {} }; new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, module, module.exports)
const { rendered, services: content, home } = module.exports
const seoOf = (metadata, html) => ({ title: metadata.title, description: metadata.description, canonical: metadata.alternates.canonical, openGraph: { url: metadata.openGraph.url, title: metadata.openGraph.title, description: metadata.openGraph.description }, twitter: { title: metadata.twitter.title, description: metadata.twitter.description }, jsonLd: JSON.parse(new JSDOM(html).window.document.querySelector('script[type="application/ld+json"]').textContent) })

// Section TYPES are permanent: <typePrefix><index> where index identifies the section, not its
// position. 215679e inserted "Dune cedar xenon yonde" before Contact; it is index 5, so drafts'
// Contact (index 4) keeps its type and ID.
const labels = ['Intro', 'When this can help', 'What we can build', 'Measure progress & related', 'Contact', 'Dune cedar xenon yonde']
const descriptions = ['Service name, promise (the only H1) and description. Name and description also set the page title, meta description and structured data.', 'When this can help: the service’s signals list.', 'What we can build with you: capability rows.', 'How we measure progress, with related-service links and a link to all services.', 'Closing call to action.', 'The service’s practical starting point, scope note and call to action (the Intro “Where to start” anchor).']
const typeOrder = [0, 1, 2, 3, 5, 4]
// The homepage's own metadata and JSON-LD (read-only reference for SEO validation; homepage.json is unchanged).
const output = { source: repository, commit, homeSeo: seoOf(home.metadata, home.html), pages: {} }
for (const [id, page] of services) {
  const { html, metadata } = rendered[id]
  const service = content[page.content]
  if (!service || service.href !== page.route) throw Error(`Page ${id} does not match content/services.ts ${page.content}.`)
  const doc = new JSDOM(html).window.document
  doc.querySelectorAll('script').forEach(n => n.remove())
  const sections = [...doc.querySelector('main').children].map(el => {
    const texts = {}; let n = 0
    const walk = node => { for (const child of [...node.childNodes]) { if (child.nodeType === 3 && child.textContent.trim() && !node.closest('svg')) { const key = `text${n++}`; texts[key] = child.textContent; const span = doc.createElement('studio-text'); span.setAttribute('data-field', key); child.replaceWith(span) } else if (child.nodeType === 1) walk(child) } }
    walk(el)
    return { type: null, label: null, description: null, html: el.outerHTML, texts }
  })
  const order = sections.length === 5 ? [0, 1, 2, 3, 4] : typeOrder // 5 = pre-215679e template
  sections.forEach((section, i) => { const index = order[i]; Object.assign(section, { type: `${page.typePrefix}${index}`, label: labels[index], description: descriptions[index] }) })
  if (sections.length !== order.length) throw Error(`${id}: ServicePage structure changed (${sections.length} sections).`)
  // Where the content schema's SEO-bearing fields render (the Intro section: label, H1, lead).
  const find = value => { for (const s of sections) for (const [field, text] of Object.entries(s.texts)) if (text === value) return { section: s.type, field }; throw Error(`${id}: ${value} not rendered`) }
  const seoFields = { name: find(service.name), promise: find(service.promise), description: find(service.description) }
  if (seoFields.name.section !== sections[0].type || seoFields.description.section !== sections[0].type) throw Error(`${id}: name/description moved out of the intro.`)
  const related = service.related.map(r => r.href)
  // The route's title is the visible service name plus a fixed source suffix (215679e: " rchid indigo xenona").
  if (typeof metadata.title !== 'string' || !metadata.title.startsWith(service.name)) throw Error(`${id}: title no longer starts with the service name.`)
  output.pages[id] = {
    route: page.route, content: page.content, sections, seoFields, related, titleSuffix: metadata.title.slice(service.name.length),
    seo: seoOf(metadata, html),
  }
}
for (const [id, page] of allServices.filter(([, p]) => p.draftOnly)) {
  const [sourceId, sourcePage] = services.find(([, p]) => p.content === page.content)
  const value = JSON.parse(JSON.stringify(output.pages[sourceId]).replaceAll(sourcePage.route, page.route).replaceAll(`"type":"${sourcePage.typePrefix}`, `"type":"${page.typePrefix}`).replaceAll(`"section":"${sourcePage.typePrefix}`, `"section":"${page.typePrefix}`))
  output.pages[id] = { ...value, route: page.route }
}
fs.writeFileSync(path.join(out, 'services.json'), JSON.stringify(output, null, 1) + '\n')
const files = ['app/page.tsx', 'components/ServicePage.tsx', 'components/Editorial.tsx', 'components/JsonLd.tsx', 'components/SiteArrow.tsx', 'components/Header.tsx', 'components/Footer.tsx', 'content/services.ts', 'lib/commercial.ts', 'lib/config.ts', 'app/layout.tsx', 'app/globals.css', ...services.map(([, p]) => `app${p.route}/page.tsx`)]
const manifest = { commit, files: Object.fromEntries(files.map(f => [f, crypto.createHash('sha256').update(fs.readFileSync(path.join(source, f))).digest('hex')])) }
fs.writeFileSync(path.join(out, 'service-manifest.json'), JSON.stringify(manifest, null, 2) + '\n')
console.log(`Imported ${services.length} service pages (${services.map(([id]) => id).join(', ')}) at ${commit.slice(0, 12)} without changing the source checkout`)
