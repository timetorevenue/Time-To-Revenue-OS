// Owner-created pages and posts: settings validation (pages.js) and their source (site-pages.mjs):
// a new route file with metadata and JSON-LD, a blog post record appended to content/blog.ts, the body
// registered in app/blog/[slug]/page.tsx, sitemap entries, refusal on source drift. No network.
import test from 'node:test'
import assert from 'node:assert/strict'
import {applyOps, codeProblem} from '../src/studio/live/code.js'
import {settingsProblem, pagesProblem, studioPages, docKey} from '../src/studio/live/pages.js'
import {applySitePages, landingSource, postSource, componentName} from '../scripts/studio-live/site-pages.mjs'

const BLOG = `export type BlogPost = {
  title: string;
  coverImageAlt?: string;
  status: "published" | "coming-soon" | "draft";
};

export const blogPosts: BlogPost[] = [
  {
    title: "Existing",
    slug: "existing",
    status: "published",
  }
];
`
const SLUG_PAGE = `import { SITE } from "@/lib/config";
// Import published post content components here as posts go live.
import Existing from "@/content/blog-posts/existing";

const publishedComponents: Record<string, React.ComponentType> = {
  "existing": Existing,
};

export async function generateMetadata() {
  const post = {coverImage: undefined};
  return {
    openGraph: { images: [{ url: \`\${SITE.domain}/images/site-og.png\` }] },
    twitter: { images: [\`\${SITE.domain}/images/site-og.png\`] },
  };
}
`
const SITEMAP = `export default function sitemap() {
  const base = "x";
  const now = "y";
  const staticRoutes = [
    { url: base, lastModified: now }
  ];
  return [...staticRoutes];
}
`
const files = new Map([['content/blog.ts', BLOG], ['app/blog/[slug]/page.tsx', SLUG_PAGE], ['app/sitemap.ts', SITEMAP]])
const created = applyOps({x: []}, {}, [
 {op: 'create_page', settings: {slug: 'spring-offer', title: 'Spring offer', description: 'Automate follow-up.', status: 'published'}},
 {op: 'create_post', settings: {slug: 'clinic-follow-up', title: 'Clinic "follow-up"', description: 'Why clinics lose patients.', date: '2026-11-23', category: 'Ops', tags: ['a'], status: 'published', coverImage: 'asset:' + 'c'.repeat(64)}},
])

test('settings validation matches the Python authority on the key cases', () => {
 const base = {kind: 'landing', slug: 'spring-offer', template: 'campaign', title: 'Spring', description: 'x', status: 'draft'}
 assert.equal(settingsProblem('app/spring-offer/page.tsx', base), null)
 assert.match(settingsProblem('app/blog/page.tsx', {...base, slug: 'blog'}), /reserved/)
 assert.match(settingsProblem('app/spring-offer/page.tsx', {...base, ogImage: 'https://evil.example.com/x.png'}), /Studio image asset/)
 assert.match(settingsProblem('app/spring-offer/page.tsx', {...base, title: 'A <b>'}), /plain text/)
 assert.match(pagesProblem({'app/other/page.tsx': base}), /does not match/)
 assert.match(codeProblem({pages: created.code.pages, files: {'app/spring-offer/page.tsx': 'x'}}), /owner-created page/)
 assert.equal(docKey('content/blog-posts/clinic-follow-up.tsx'), 'post-clinic-follow-up')
})

test('created pages appear in the editor page list; posts use the blog layout bound to their body', () => {
 const model = {pages: [{id: 'blog-slug:existing', route: '/blog/existing', doc: 'app/blog/[slug]/page.tsx', bind: {'blog-slug_1r': 'content/blog-posts/existing.tsx'}}]}
 const list = studioPages(created.code.pages, model)
 assert.deepEqual(list.map(p => [p.id, p.route, p.doc]), [['created:spring-offer', '/spring-offer', 'app/spring-offer/page.tsx'],
  ['created-post:clinic-follow-up', '/blog/clinic-follow-up', 'app/blog/[slug]/page.tsx']])
 assert.equal(list[1].bind['blog-slug_1r'], 'content/blog-posts/clinic-follow-up.tsx')
})

test('blog record, registry, cover image field and sitemap entries are written into the site source', () => {
 const out = applySitePages(created.code.pages, files, {}, created.lists, {assetPath: ref => `/studio-assets/${ref.slice(6)}.webp`})
 const blog = out['content/blog.ts']
 assert.match(blog, /slug: "clinic-follow-up",/)
 assert.match(blog, /title: "Clinic \\"follow-up\\"",/)
 assert.match(blog, /coverImage: "\/studio-assets\/c{64}\.webp",/)
 assert.match(blog, /coverImage\?: string;/)
 assert.match(blog, /status: "published",\n  },\n  \/\/ ── Created in TTROS Website Studio ──/, 'the comma is added after the last existing record')
 assert.match(blog, /readingTime: "\d+ min read",/)
 const page = out['app/blog/[slug]/page.tsx']
 assert.match(page, /import StudioPostClinicFollowUpContent from "@\/content\/blog-posts\/clinic-follow-up";/)
 assert.match(page, /"clinic-follow-up": StudioPostClinicFollowUpContent,/)
 assert.equal(page.split('post.coverImage ?? "/images/site-og.png"').length, 3)
 assert.match(out['app/sitemap.ts'], /url: `\$\{base\}\/spring-offer`,/)
 assert.equal(componentName('post', '4-step-guide'), 'StudioPostN4StepGuideContent')
})

test('drafts and noindex pages stay out of the sitemap; drift is refused', () => {
 const draft = {...created.code.pages, 'app/spring-offer/page.tsx': {...created.code.pages['app/spring-offer/page.tsx'], noindex: true}}
 assert.equal(applySitePages(draft, files, {}, created.lists)['app/sitemap.ts'], undefined)
 const drifted = new Map(files); drifted.set('app/blog/[slug]/page.tsx', SLUG_PAGE.replace('// Import published post content components here as posts go live.\n', ''))
 assert.throws(() => applySitePages(created.code.pages, drifted, {}, created.lists), /Website source drift/)
})

test('landing and post sources carry metadata, JSON-LD and only the imports they use', () => {
 const s = created.code.pages['app/spring-offer/page.tsx']
 const src = landingSource('app/spring-offer/page.tsx', s, '<Header />\n<main />', new Set(['kit:Header', 'Link', 'comp:Calc:@/components/Calc']))
 assert.match(src, /export const metadata: Metadata = \{/)
 assert.match(src, /alternates: \{ canonical: `\$\{SITE.domain\}\/spring-offer` \}/)
 assert.match(src, /robots: \{ index: true, follow: true \}/)
 assert.match(src, /"@type": "WebPage"/)
 assert.match(src, /import Header from "@\/components\/Header";/)
 assert.match(src, /import Calc from "@\/components\/Calc";/)
 assert.doesNotMatch(src, /import Footer/)
 const draft = landingSource('app/spring-offer/page.tsx', {...s, status: 'draft'}, '<main />', new Set())
 assert.match(draft, /robots: \{ index: false, follow: false \}/)
 assert.match(postSource('content/blog-posts/x.tsx', {slug: 'x'}, '<p />', new Set()), /export default function StudioPostXContent\(\)/)
})
