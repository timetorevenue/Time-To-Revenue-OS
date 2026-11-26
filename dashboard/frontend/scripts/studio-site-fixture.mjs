// Deterministic, local-only bridge. Read the registered source; write only a new
// disposable /tmp fixture. Never changes Git worktrees, registry, jobs or source.
// Stage 3: one fixture can carry the homepage draft and any service-page drafts (one envelope per
// page). The homepage path is unchanged from Stage 1/2. Each service page renders its real
// ServicePage.tsx through the verified-template adapter; its metadata and JSON-LD come from the
// same validated page model (seo.js), and its design tokens are scoped to that page in the shared
// stylesheet. Routes, slugs and canonical URLs are never changed.
// Usage: node scripts/studio-site-fixture.mjs draft.json [draft.json ...] [read-only-source-dir]
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import crypto from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'
import { validateEnvelope, exportDraft, envelopePage, snapshot } from '../src/studio/state.js'
import manifest from '../src/studio/source-manifest.json' with { type:'json' }
import serviceManifest from '../src/studio/service-manifest.json' with { type:'json' }
import pages from '../src/studio/pages.json' with { type:'json' }
import { effectiveDesign,sharedCss,rows,validateSharedContent } from '../src/studio/shared.js'
import { siteDesignCss, scopeCss } from '../src/studio/design.js'
// Schema v5: the adapter is pinned to the committed candidate, read from Git objects (git archive /
// git show). The registered checkout's working tree and HEAD are never read for building or written.
export const PREVIEW_CANDIDATE='215679e81711553cf572acff71b60f42f2b2181d'
if(manifest.commit!==PREVIEW_CANDIDATE||serviceManifest.commit!==PREVIEW_CANDIDATE)throw Error('Studio adapter manifests and preview candidate disagree.')
const frontend=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..')
export function verifySource(source) {
 const sha=execFileSync('git',['-C',source,'rev-parse','--verify',`${manifest.commit}^{commit}`],{encoding:'utf8'}).trim()
 if(sha!==manifest.commit || sha!==serviceManifest.commit)throw Error('Pinned website commit is missing; refresh/review the adapter before export.')
 for(const [relative,hash]of Object.entries({...manifest.files,...serviceManifest.files})) {
  const blob=execFileSync('git',['-C',source,'show',`${manifest.commit}:${relative}`],{maxBuffer:64*1024*1024,stdio:['ignore','pipe','ignore']})
  if(crypto.createHash('sha256').update(blob).digest('hex')!==hash)throw Error(`Website source drift: ${relative}`)
 }
}
function replaceOnce(text,before,after,label) {
 if(text.split(before).length!==2)throw Error(`Website ${label} drift.`)
 return text.replace(before,after)
}
export async function createSiteFixture(input,{source=snapshot.source,fixtureRoot=process.env.TTROS_STUDIO_FIXTURE_ROOT||os.tmpdir(),candidate=false,copyDependencies=true}={}) {
 const envelopes=Array.isArray(input)?input:[input]
 const drafts={}
 for(const envelope of envelopes) {
  const page=envelopePage(envelope)
  if(drafts[page])throw Error(`Two drafts for page ${page}.`)
  drafts[page]=validateEnvelope(envelope)
 }
 for(const envelope of envelopes)if(envelope.sharedSite) {
 const shared=validateEnvelope(envelope.sharedSite,'site')
 if(drafts.site && JSON.stringify(drafts.site)!==JSON.stringify(shared))throw Error('Shared site drafts disagree; export again from one session.')
 drafts.site=shared
 }
 if(!Object.keys(drafts).length)throw Error('No Studio drafts given.')
 source=fs.realpathSync(source)
 const revision=manifest.commit
 verifySource(source)
 const folder=fs.mkdtempSync(path.join(fixtureRoot,'ttros-studio-site-'))
 const archive=path.join(folder,'source.tar')
 const entries=execFileSync('git',['-C',source,'ls-tree','--name-only',revision],{encoding:'utf8'}).trim().split('\n').filter(p=>['app','components','content','lib','public','package.json','package-lock.json','tsconfig.json','next-env.d.ts','next.config.ts','postcss.config.mjs','tailwind.config.ts','eslint.config.mjs','.eslintrc.json'].includes(p))
 execFileSync('git',['-C',source,'archive','--format=tar','--output',archive,revision,...entries])
 execFileSync('tar',['-x','-C',folder,'-f',archive])
 // Copy, never symlink, dependencies: a build cannot mutate the source cache.
 const dependencies=path.join(source,'node_modules')
 if(copyDependencies&&fs.existsSync(dependencies))fs.cpSync(dependencies,path.join(folder,'node_modules'),{recursive:true})
 const bundle=await build({entryPoints:[path.join(frontend,'src/studio/site-tree.jsx')],bundle:true,write:false,platform:'node',format:'esm',external:['react'],jsx:'automatic'})
 const relativeFiles=['components/Editorial.tsx','app/globals.css','lib/ttros-studio-model.js','lib/ttros-studio.tsx']
 fs.writeFileSync(path.join(folder,'lib/ttros-studio-model.js'),'// Generated from the shared TTROS Studio model.\n'+bundle.outputFiles[0].text)
 const services=Object.keys(drafts).filter(p=>p!=='home'&&p!=='site')
 const lines=[`import { adaptHomeChildren, adaptPageChildren, pageSeo } from './ttros-studio-model';`,`import WorkflowFigure from '@/components/WorkflowFigure';`,`import { Arrow } from '@/components/SiteArrow';`,`import type { ReactNode } from 'react';`]
 if(drafts.home) {
  relativeFiles.unshift('app/page.tsx'),relativeFiles.push('content/ttros-studio-draft.json')
  fs.writeFileSync(path.join(folder,'content/ttros-studio-draft.json'),JSON.stringify(exportDraft(drafts.home,'home'),null,2)+'\n')
  lines.push(`import draft from '@/content/ttros-studio-draft.json';`)
 }
 for(const [index,page] of services.entries()) {
  const file=`content/ttros-studio-${page}.json`
  relativeFiles.push(file)
  fs.writeFileSync(path.join(folder,file),JSON.stringify(exportDraft(drafts[page],page),null,2)+'\n')
  lines.push(`import page${index} from '@/${file.replace(/\.json$/,'.json')}';`)
 }
 lines.push(`const pages: Record<string, { data: unknown }> = { ${services.map((p,i)=>`'${p}': page${i}`).join(', ')} };`)
 lines.push(drafts.home?`export function renderStudioHomepage(children: ReactNode, components: unknown[]) {\n return adaptHomeChildren(children,draft.data,[...components,WorkflowFigure,Arrow],{WorkflowFigure});\n}`:`export function renderStudioHomepage(children: ReactNode, _components?: unknown[]): ReactNode {\n return children;\n}`)
 lines.push(`function pageDraft(page: string) {\n const value = pages[page];\n if (!value) throw new Error('No Studio draft for ' + page);\n return value.data;\n}`)
 lines.push(`export function renderStudioPage(page: string, children: ReactNode, components: unknown[]) {\n return adaptPageChildren(children,pageDraft(page),[...components,Arrow],page);\n}`)
 lines.push(`export function studioPageSeo(page: string): { title: string; description: string } {\n return pageSeo(page,pageDraft(page));\n}`)
 fs.writeFileSync(path.join(folder,'lib/ttros-studio.tsx'),lines.join('\n')+'\n')
 if(drafts.home) {
  const home=fs.readFileSync(path.join(folder,'app/page.tsx'),'utf8')
  if(!home.includes('<EditorialPage>'))throw Error('Website homepage wrapper drift.')
  fs.writeFileSync(path.join(folder,'app/page.tsx'),home.replace('<EditorialPage>','<EditorialPage studioHome>'))
 }
 let editorial=fs.readFileSync(path.join(folder,'components/Editorial.tsx'),'utf8')
 const signature='export function EditorialPage({ children }: { children: ReactNode })'
 if(!editorial.includes(signature)||!editorial.includes('tabIndex={-1}>{children}</main>'))throw Error('Website editorial wrapper drift.')
 editorial='import { renderStudioHomepage, renderStudioPage } from "@/lib/ttros-studio";\n'+editorial.replace(signature,'export function EditorialPage({ children, studioHome = false, studioPage }: { children: ReactNode; studioHome?: boolean; studioPage?: string })')
  .replace('tabIndex={-1}>{children}</main>','tabIndex={-1} data-studio-page={studioHome ? "home" : studioPage}>{studioHome ? renderStudioHomepage(children, [ContactBand, DiscussionLink, TextLink]) : studioPage ? renderStudioPage(studioPage, children, [PageIntro, ContactBand, DiscussionLink, TextLink]) : children}</main>')
 fs.writeFileSync(path.join(folder,'components/Editorial.tsx'),editorial)
 if(services.length) {
  relativeFiles.push('components/ServicePage.tsx')
  let template=fs.readFileSync(path.join(folder,'components/ServicePage.tsx'),'utf8')
  template=replaceOnce(template,'export default function ServicePage({ service }: { service: Service })','export default function ServicePage({ service, studioPage }: { service: Service; studioPage?: string })','ServicePage signature')
  template=replaceOnce(template,'    <EditorialPage>\n','    <EditorialPage studioPage={studioPage}>\n','ServicePage wrapper')
  fs.writeFileSync(path.join(folder,'components/ServicePage.tsx'),template)
  for(const page of services) {
   const route=`app${pages.pages[page].route}/page.tsx`
   relativeFiles.push(route)
   if(page==='test-offer') {
    fs.mkdirSync(path.dirname(path.join(folder,route)),{recursive:true});
    fs.writeFileSync(path.join(folder,route),execFileSync('git',['-C',source,'show',`${revision}:app/services/business-process-automation/page.tsx`]));
   }
   let text=fs.readFileSync(path.join(folder,route),'utf8')
   if(page==='test-offer')text=text.replace('const service = services.process;',`const service = { ...services.process, href: '/offers/test-workflow-audit' };`);
   text=replaceOnce(text,'export const metadata = commercialMetadata(service.href, `${service.name} in Example City, EX`, service.description);',`// TTROS Studio: title (service name + the source's " rchid indigo xenona") and description follow the page's validated visible text.\nconst studio = studioPageSeo(${JSON.stringify(page)});\nexport const metadata = commercialMetadata(service.href, studio.title, studio.description);`,`${route} metadata`)
   text=replaceOnce(text,'<ServicePage service={service} />',`<ServicePage service={service} studioPage=${JSON.stringify(page)} />`,`${route} template`)
   text=`import { studioPageSeo } from "@/lib/ttros-studio";\n`+text
   fs.writeFileSync(path.join(folder,route),text)
  }
 }
 // Shared defaults, with legacy homepage exports preserved.
 const site=drafts.site
 // Source-first: only deliberate differences are appended (siteDesignCss). The Shared site design is
 // compared with the defaults; each page's own design with the Shared site design it inherits.
 // Local button appearances (buttonVariant) read the Studio-private custom properties.
 const privateVars=JSON.stringify(drafts).includes('"buttonVariant"')
 const css=site ? '\n'+siteDesignCss(site.root.props.design,undefined,{privateVars})+sharedCss(site,{site:true})+Object.entries(drafts).filter(([p])=>p!=='site').map(([p,d])=>scopeCss(siteDesignCss(effectiveDesign(d,site),site.root.props.design,{page:true}),p)).join('\n') : [drafts.home?'\n'+siteDesignCss(drafts.home.root.props.design,undefined,{privateVars}):'',...services.map(p=>scopeCss(siteDesignCss(drafts[p].root.props.design,undefined,{page:true}),p))].join('\n')
 fs.appendFileSync(path.join(folder,'app/globals.css'),css)
 if(site) {
  validateSharedContent(site.content,drafts)
  const header=site.content.find(i=>i.type==='SharedHeader').props,footer=site.content.find(i=>i.type==='SharedFooter').props
  const configFile='content/ttros-studio-shared.json'
  fs.writeFileSync(path.join(folder,configFile),JSON.stringify({header,footer,navigation:rows({props:header},'navigation'),learning:rows({props:footer},'learning'),legal:rows({props:footer},'legal')},null,2))
  relativeFiles.push(configFile,'components/Header.tsx','components/Footer.tsx')
  for(const [kind,p] of [['Header',header],['Footer',footer]]) {
   const file=path.join(folder,`components/${kind}.tsx`)
   let text=fs.readFileSync(file,'utf8')
   text=text.replace('import { CONTACT, DISCUSS, navigation } from "@/lib/commercial";',`import shared from '@/content/ttros-studio-shared.json';\nconst { navigation } = shared;\nconst fields = shared.${kind==='Header'?'header':'footer'};\nconst CONTACT = fields.destination;\nconst DISCUSS = fields.cta;`)
   text=text.replace('import { SITE } from "@/lib/config";','')
   text=text.replaceAll('SITE.name','fields.name').replaceAll('SITE.email','fields.email').replaceAll(kind==='Header'?'/images/site-logo-dark.svg':'/images/site-logo-light.png',p.logo)
   if(kind==='Footer') {
    text=replaceOnce(text,'Umber fern meadow grove cedar ridge summit heron orchida','{fields.tagline}','footer description')
    text=replaceOnce(text,'>Explore</p>','>{fields.explore}</p>','Explore heading')
    text=replaceOnce(text,'>Learn &amp; plan</p>','>{fields.learn}</p>','Learning heading')
    text=text.replace(/<ul>\s*<li><Link href="\/blog">[\s\S]*?<\/ul>/,'<ul>{shared.learning.map(({id,href,label}) => <li key={id}><Link href={href}>{label}</Link></li>)}</ul>')
    text=text.replace(/<Link href="\/privacy">[\s\S]*?<Link href="\/terms">Terms<\/Link>/,'{shared.legal.map(({id,href,label}) => <Link key={id} href={href}>{label}</Link>)}')
   }
   fs.writeFileSync(file,text)
  }
 }
 if(candidate) {
  // Isolated preview only: no external tracking, forms or connector requests.
  const configFile=path.join(folder,'next.config.ts')
  let config=fs.readFileSync(configFile,'utf8')
  config=config.replace('const nextConfig: NextConfig = {','const nextConfig: NextConfig = {\n  experimental: { cpus: 2 },')
  config=config.replace('headers: securityHeaders,', `headers: [...securityHeaders.filter(h=>!['X-Frame-Options','Content-Security-Policy-Report-Only'].includes(h.key)), {key:'Content-Security-Policy',value:"default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-src 'none'; form-action 'none'; frame-ancestors http://127.0.0.1:3010 http://localhost:3010"}],`)
  fs.writeFileSync(configFile,config)
  relativeFiles.push('next.config.ts')
 }
 const allFiles={...manifest.files,...serviceManifest.files}
 const changes=relativeFiles.map(relative=>({path:relative,before_sha256: (()=>{try{return crypto.createHash('sha256').update(execFileSync('git',['-C',source,'show',revision+':'+relative],{stdio:['ignore','pipe','ignore']})).digest('hex')}catch{return null}})(),after_sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(folder,relative))).digest('hex')}))
 const draftHashes=Object.fromEntries(Object.entries(drafts).map(([page,data])=>[page,crypto.createHash('sha256').update(JSON.stringify(exportDraft(data,page))).digest('hex')]))
 const receipt={fixture:folder,base:revision,pages:Object.keys(drafts),changes,sourceUntouched:true,modelCalls:0,publication:'held',build:'not_run',...(drafts.home?{draftSha256:draftHashes.home}:{}),pageDraftSha256:draftHashes}
 fs.writeFileSync(path.join(folder,'studio-adapter-receipt.json'),JSON.stringify(receipt,null,2)+'\n')
 verifySource(source)
 return receipt
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
 const args=process.argv.slice(2)
 if(!args.length)throw Error('Usage: node scripts/studio-site-fixture.mjs draft.json [draft.json ...] [read-only-source-dir]')
 const sourceArg=args.length>1&&fs.statSync(args.at(-1)).isDirectory()?args.pop():snapshot.source
 const result=await createSiteFixture(args.map(file=>JSON.parse(fs.readFileSync(file,'utf8'))),{source:sourceArg})
 console.log(JSON.stringify(result,null,2))
}
