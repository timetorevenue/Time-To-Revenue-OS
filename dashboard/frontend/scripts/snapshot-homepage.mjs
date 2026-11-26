// Explicit read-only import; never runs Next or writes into the website checkout.
import { build } from 'esbuild'
import { JSDOM } from 'jsdom'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import { snapshotSource } from './studio-source-archive.mjs'
const require = createRequire(import.meta.url)
// Usage: node scripts/snapshot-homepage.mjs [repository] [--commit <sha>] (--commit reads a git archive)
const { repository, root: source, commit } = snapshotSource(process.argv.slice(2))
const out = path.resolve('src/studio')
const result = await build({ stdin: { contents: `import React from 'react'; import {renderToStaticMarkup} from 'react-dom/server'; import Home from '${source}/app/page.tsx'; export default renderToStaticMarkup(React.createElement(Home));`, resolveDir: process.cwd(), loader: 'tsx' }, bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic', plugins: [{name: 'snapshot-shims', setup(b) {
 b.onResolve({filter: /^react($|\/)|^react-dom\//}, a => ({path: require.resolve(a.path), external: true}))
 b.onResolve({filter: /^@\//}, a => ({path: path.join(source,a.path.slice(2))+ (fs.existsSync(path.join(source,a.path.slice(2))+'.tsx')?'.tsx':'.ts')}))
 b.onResolve({filter: /^next\//}, a => ({path:a.path, namespace:'shim'}))
 b.onLoad({filter: /.*/, namespace:'shim'}, a => ({loader:'jsx', contents: a.path==='next/navigation' ? `export const usePathname=()=>'/';` : `import React from 'react'; export default function Shim({children,fill,priority,sizes,unoptimized,...p}){return React.createElement('${a.path==='next/image'?'img':'a'}',p,children)}` }))
}}] })
const module = {exports:{}}; new Function('require','module','exports',result.outputFiles[0].text)(require,module,module.exports)
const doc = new JSDOM(module.exports.default).window.document
// Strip structured data and inactive navigation; keep every actual homepage section.
doc.querySelectorAll('script').forEach(n=>n.remove())
const labels = ['Hero','Delta bravo jade embe','Outcomes','Solutions','Iris bravoa','Harbor k','Founder','Common questions','Contact']
// Section elements keep their permanent positional types (Section0…Section8). Since 215679e the hero
// is followed by a hero-signature <div>; it is its own permanent type, never renumbering the sections.
const companions = { 'hero-signature editorial-shell': ['HeroSignature', 'Hero signature'] }
let sectionIndex = 0
const sections = [...doc.querySelector('main').children].map(el=>{
 let texts={}; let n=0
 const walk = node => { for(const child of [...node.childNodes]) {if(child.nodeType===3 && child.textContent.trim() && !node.closest('svg')) {const key=`text${n++}`; texts[key]=child.textContent; const span=doc.createElement('studio-text'); span.setAttribute('data-field',key); child.replaceWith(span)} else if(child.nodeType===1) walk(child) } }
 walk(el)
 if(el.tagName!=='SECTION') {
  const companion=companions[el.getAttribute('class')]
  if(!companion)throw Error(`Unknown homepage element <${el.tagName.toLowerCase()} class="${el.getAttribute('class')}">`)
  return {type:companion[0],label:companion[1],html:el.outerHTML,texts}
 }
 const i=sectionIndex++
 return {type:`Section${i}`,label:labels[i],html:el.outerHTML,texts}
})
const css = fs.readFileSync(path.join(source,'app/globals.css'),'utf8').split('/* Shared marketing layouts */')[1]
fs.writeFileSync(out+'/homepage.json',JSON.stringify({source:repository,commit,sections},null,2)+'\n')
// The homepage adapter's pinned source files (hashes of the committed blobs).
const pinned=['app/page.tsx','app/layout.tsx','app/globals.css','components/Editorial.tsx','components/WorkflowFigure.tsx','components/SiteArrow.tsx','components/Header.tsx','components/Footer.tsx','components/JsonLd.tsx','content/services.ts','lib/commercial.ts','lib/config.ts']
fs.writeFileSync(out+'/source-manifest.json',JSON.stringify({commit,files:Object.fromEntries(pinned.map(f=>[f,crypto.createHash('sha256').update(fs.readFileSync(path.join(source,f))).digest('hex')]))},null,2)+'\n')
fs.writeFileSync('public/studio/homepage.css',`/* Read-only homepage style snapshot. See src/studio/homepage.json provenance. */\n*{box-sizing:border-box}body{margin:0;background:#f7f3ea;color:#111315;font:16px/1.65 Inter,Arial,sans-serif}h1,h2,h3,p,ul,ol,figure{margin:0}a{color:inherit;text-decoration:none}button{font:inherit}img{max-width:100%}\n@font-face{font-family:Inter;src:url('/studio/InterVariable.woff2')}@font-face{font-family:Geist;src:url('/studio/Geist_wght_.woff2')} :root{--font-geist:Geist;--font-inter:Inter}\n`+css.replaceAll('.home-hero h1 span', '.home-hero h1 > span:not([data-puck-overlay-portal])'))
for(const name of ['InterVariable.woff2','Geist_wght_.woff2']) fs.copyFileSync(path.join(source,'public/fonts',name),'public/studio/'+name)
fs.copyFileSync(path.join(source,'public/images/casey-profile-transparent.webp'),'public/studio/casey-profile-transparent.webp')
// The only image in the editable body is local to this proof.
const data=JSON.parse(fs.readFileSync(out+'/homepage.json')); data.sections.forEach(s=>s.html=s.html.replaceAll('/images/casey-profile-transparent.webp','/studio/casey-profile-transparent.webp')); fs.writeFileSync(out+'/homepage.json',JSON.stringify(data,null,2)+'\n')
console.log(`Imported ${sections.length} sections without changing source checkout`)
