# Website Studio — live website visual editor

> Revisit: when the live website source moves past the pinned commit, the model schema changes, or
> Puck is upgraded. · Last touched: 2026-11-23.

The default **Website Studio → Live website editor** edits the real Next.js source of the live
Example Company website (Vercel production source `Time-To-Revenue/timetorevenue-site@2e521be`),
element by element, inside the existing Studio (same Puck editor, Sources & Branding, Releases).
The preserved 215679e candidate section editor stays available as **Preserved candidate**; the
published-page capture editor stays reachable from the Websites tab history. Zero model calls.

## How it works

1. **Import** (`scripts/studio-live/import-live.mjs`, button *Import live website*, operator only).
   Reads the pinned commit from Git objects only (never the checkout's working tree), parses every
   page, shared component (Header, Footer, HeroDiagram) and blog post body with Babel, and turns
   each rendered JSX element into a model node (`src/studio/live/model.js`):
   `El` (element), `Txt` (text run), `Cond` (a source condition whose branches are editable),
   `Use` (functional component or shared document) and `Code` (data-driven part).
   Static values are evaluated in a sandbox (`node:vm`, no Date/randomness/IO) so lists over local
   constants (navigation, cards, steps) and small local wrapper components (`<P>`,
   `<SectionHeading>`) unroll into individually editable elements.
   The import then builds the site twice in a disposable folder — pristine, and fully regenerated
   from the model with capture markers — crawls every prerendered route, and records the built
   stylesheet/fonts for the canvas, rendered values of code-bound attributes, the HTML of
   functional parts, the branch each condition shows on load, and a **parity report**
   (2e521be: 46/46 routes render identically — body DOM, title, meta, canonical, JSON-LD — when
   every element is regenerated from the model).
2. **Edit.** Puck slots render *as the site element itself*, so selection, drag-and-drop,
   reparenting, duplicate and delete act on the real markup. On-canvas handles: free move
   (offset or absolute, per breakpoint), 8-point resize, and *drag into container* (deepest
   container under the pointer, insertion line, one validated move). Inspector: text, links,
   media, embed address, classes, anchor id, element type, condition branch, component content
   (e.g. FAQ items), position mode, 1–6 columns / row / stack presets and every style property
   for Desktop (base), Tablet (≤1023px) and Mobile (≤767px). Palette: heading, paragraph, inline
   text, list, quote, button, link, image, video embed, icon, divider, container, section,
   2/3 columns, grid, row, stack, card, background block, free-position canvas, spacer,
   expandable, anchor, form/booking embed. Assets: upload (PNG/JPEG/GIF/WebP/AVIF/SVG/MP4/WebM,
   content-addressed sha256), the website's own `public/` files, and approved Google Drive files
   (registered brand assets or the selected website asset folder in Sources) via the existing
   Drive reader — each with provenance.
3. **Undo/redo, autosave, history.** Every change is one Puck history step (Ctrl/Cmd+Z,
   Ctrl/Cmd+Shift+Z, Ctrl+Y, toolbar). Work autosaves to IndexedDB and to the server autosave
   slot; each page's undo history persists in IndexedDB, so close/crash/reopen recovers the work
   *and* Undo. *Save version* (Ctrl/Cmd+S) appends an immutable, timestamped, hash-chained
   version (content-addressed document blobs). Versions can be previewed; *Restore* appends a new
   version (nothing is overwritten) and is itself undoable on the canvas.
4. **Build.** *Build real Next.js preview* regenerates only edited elements into the pinned
   source in a disposable fixture (untouched documents and subtrees are re-emitted byte for byte),
   writes owner styles to `app/ttros-studio.css` (imported by `app/layout.tsx`), copies used
   assets to `public/studio-assets/`, runs `npm ci` + `next build` with integrations disabled and
   no inherited credentials, and serves it on loopback for 30 minutes with the exact source diff.
   *Build release candidate* keeps the website's own `next.config.ts`; its validated build becomes
   an immutable release in **Releases** (`adapter: live`), with the existing typed-confirmation
   publish and rollback. Release builds run `vercel build --prod`; publishing to core-site.example
   stages that exact output on Vercel, checks it, promotes it and can roll back
   (docs/website_studio_releases.md).

## Safety model

- Drafts carry literal values only. Source code (event handlers, state, data bindings, scripts,
  JSON-LD) is referenced by id into the server-side import, never authored. An element that uses
  a file's code can move anywhere *within* that file; moving it into another file is refused
  (canvas, server validator and generator). Source text is only re-emitted inside its own file.
- `website_studio_live.validate_tree` is authoritative: allow-listed tags/attributes/style
  properties, URL checks (no `javascript:`/`data:` except base64 images), frames only to approved
  embed hosts, background images only from the asset library, no `on*`/`style`/raw HTML
  literals, unique ids, size limits. SVG uploads with scripts/handlers/external references are
  refused. All mutations need the TTROS operator and the Dashboard CSRF header; saves are
  refused when stale (409).
- Builds read Git objects and re-verify every imported file hash before and after building.

## Coverage (2e521be, importer of 2026-11-22)

47 Studio pages: 21 static pages, 12 blog posts with editable bodies, the placeholder post and 12
resource pages (through their templates), and the static guide `/guides/4-prompt-project-workflow`.
Regenerating every element from the model renders all 47 identically (pristine vs regenerated build).

- **Repeated templates (`Map`)**: lists whose entries are not static — FAQ items (component props),
  blog post tags/related/cluster lists, resource categories and cards — import as one editable
  template. Moving, restyling, adding or removing elements inside it changes every entry; entry data,
  keys and handlers stay source references bound to the template and cannot leave it (canvas,
  validators and generator refuse). Templates with several roots are emitted as keyed Fragments.
- **Component templates**: `components/FaqAccordion.tsx` imports as a template document, composed in
  place on each page that uses it; the accordion keeps its state and handlers. FAQ questions/answers
  remain that page's component content.
- **Data records (`Rec`)**: `content/blog.ts` and `content/resources.ts` import as records keyed by
  slug; their plain string fields (by the TypeScript type; never slug/pillar/contentPath, never
  union-typed fields such as resource category) are edited from the post/resource page or by selecting
  a bound value, and written into their literal positions, so page, cards, sitemap and SEO change
  together. Records cannot be added, removed or reordered in the editor.
- **State-dependent text** (`Open menu`/`Close menu`) imports as condition branches; components' load
  state (`useState` initial values) decides what the canvas shows.
- **Index lists** with `if (...) return null` (blog clusters) unroll into individually editable entries.
- **Static HTML guides**: the body is edited element by element on the canvas with the guide's own
  stylesheet; owner styles are injected into the guide; the identical `guide.html` is written with it.
- **Source changes**: *Website source → Update to this source* imports a newer live commit, carries the
  latest saved version and the autosave onto it (`migrate-live.mjs`: node alignment, owner edits win,
  source additions kept, references re-bound by exact code, conflicts listed) and moves the pin.
  Older versions are carried over when restored or previewed.

Still not visual documents: generated files (robots, sitemap, icons, manifest), the Brevo webhook
route and JSON-LD scripts (preserved, verified on every publish). New Tailwind classes typed into
*Classes* render in the built site but not in the canvas. Moving an element out of a responsive
wrapper changes where it shows, exactly as the source would.

## Proof recipe (isolated)

`studio_local` with `TTROS_STUDIO_OFFLINE=1 TTROS_STUDIO_STATE=<scratch>`,
`TTROS_DASHBOARD_ORIGIN=http://127.0.0.1:4350`, worktree Vite on 4350 with
`TTROS_STUDIO_API_TARGET`/`TTROS_API_TARGET` → API. Import via the API, then
`scripts/studio-live/browser-proof.mjs` (UI end to end) and `output-proof.mjs` (editor vs built
site at 1280/390, booking form, SEO, webhook, changed files). Evidence:
`proofs/website_studio_live_editor_20261121/`.

## Stage 2 (2026-11-23): new pages and posts, voice, specialists, media

- **New landing pages** (*+ New page*, or ask the Website Manager “create a landing page called “…” at /slug”):
  a template (campaign, lead magnet/form, blank) becomes a genuinely new source file `app/<slug>/page.tsx`
  with the site's Header/Footer, metadata (title/SEO title, description, canonical, robots, keywords,
  Open Graph/Twitter image), WebPage JSON-LD and, when published and indexable, an `app/sitemap.ts`
  entry. Draft pages are generated with `noindex` and stay out of the sitemap.
- **New blog posts** (*+ New post*, or “write a blog post titled “…” in the X category tagged a, b”): the
  existing blog architecture is extended, never duplicated — a record appended to `content/blog.ts`
  (category, tags, keywords, excerpt, date, status, reading time, cover image, pillar, related posts),
  the body as `content/blog-posts/<slug>.tsx`, registered in `app/blog/[slug]/page.tsx`. Blog index,
  related posts, BlogPosting JSON-LD, robots and sitemap come from the site's own code (published only).
  `coverImage` (added to the BlogPost type when used) is the post's social image; body images are elements.
- Created pages live in the version's code layer (`pages`, `website_studio_pages.py` ↔ `pages.js`) with
  their element documents, so versions, restore, autosave, proposals, builds and releases carry them.
  Settings: **Page & SEO** tab. Validation refuses existing or reserved routes, existing post slugs,
  remote/unknown images, markup in settings, and Code-mode overrides of a created page's source.
- **Shared operations** added: `insert`, `remove`, `create_page`, `create_post`, `set_page`, `remove_page`
  (Python authority = JS twin, parity-tested). `alt` is accepted on images only (a `<video alt>` fails
  the site's type check — found by a real build).
- **Website Manager specialists** (each answer names one; deterministic first, Luna low for what the
  rules cannot read): *Design* (styles per breakpoint, layout, shadows, gradients, hover lift via owner
  CSS, inserting buttons/headings/paragraphs, removing), *Content & SEO* (new pages/posts, SEO title,
  description, excerpt, category, tags, keywords, status, social/cover image, related posts, noindex),
  *Media* (images and videos from the library, YouTube/Vimeo URLs → privacy-enhanced embeds, alt text,
  autoplay/loop/mute) and *Frontend* (owner CSS, header modes, placing a Code-mode React component;
  new logic is answered with the Code-mode path). The panel stays mounted across tabs.
- **Voice**: *Speak* dictates into the same request box and the same Send (spoken and typed requests are
  one path; the Job records `input: voice`). Browser speech recognition (Chrome, Edge, Safari) or
  *Local transcription*: the recording (WebM/Opus, Safari MP4/AAC, Ogg) is transcribed on the TTROS
  server by faster-whisper base.en on CPU (`tools/studio_transcribe.py`, venv
  `~/.local/share/ttros/stt-venv`, model `~/.local/share/ttros/models/whisper/base.en`; no network, no
  provider, no cost). *Speak replies* (off by default) reads replies with the browser's speech synthesis.
- **Media**: upload images/MP4/WebM; **image editor** (crop ratios, focus, resize, rotate, flip,
  WebP/JPEG/PNG) saves a new asset with `studio_edit` provenance; video file and gallery blocks;
  reusable sections (save a selection, insert anywhere with fresh ids); **React components** written in
  Code mode (default export without props) placed visually as `Comp` nodes (canvas placeholder, real
  render in the build). **Image generation**: no authorized provider route exists in TTROS (ModelPort is
  text-only and refuses separately billed APIs); the Assets panel says so and manual tools work.
- Proof: `scripts/studio-live/stage2-proof.mjs` (isolated Studio; fake microphone plays a WAV);
  evidence `proofs/website_studio_stage2_20261123/`.

## Workspace UX (2026-11-23; feature only, awaiting approval)

The live editor opens in an immersive workspace with **Website Manager** and its text/microphone
composer visible. **Design**, **AI**, **Code** and **Preview** are the working modes; selecting an
element opens its Inspector while keeping the manager dock available. **More Studio tools** keeps
Page & SEO, Site, Versions, Coverage, Sources & Branding, Releases and the preserved editors reachable.
**Back to Dashboard** exits immersion. Desktop Dashboard navigation collapses to a 60px icon rail,
with its preference retained independently of Studio drafts; mobile Dashboard navigation is unchanged.

The left rail opens Pages, Insert, Layers and Assets. Panels collapse and resize (left 240–360px,
right 320–480px), including Arrow keys and Home/End on their resize separators. Fit, percentage zoom,
100%, +/− and the hand tool operate on the existing Puck canvas. **Canvas only** hides both drawers
and the content rail; the labeled manager entry remains available. Narrow screens use overlay drawers.
Code text, open manager proposals and dictation survive tool changes; unfinished manager text and
proposals also survive page switches within the Studio session. Browser/server autosaves, persisted
undo histories and version storage retain their existing keys and formats. Real build progress stays
available across tool/page changes; **Expand preview** shows the real Next.js output across the window.

The pinned source Header is sticky and changes its transparent background after scrolling. Its React
listener does not run inside Puck: the canvas now simulates its source-bound scroll styles and menu
state, and uses the existing `headerState` contract for owner-configured header modes. The toolbar's
Header selector provides Auto/Top/Scrolled/Hidden/Menu previews. These states never enter saved data
or generated source; owner element styles keep their precedence. Revisit this simulation when the
pinned Header's source contract changes. No website source, backend, model route or publishing gate
was changed by this UX increment.

Proof and before/after comparison: `proofs/website_studio_ux_20261123/receipt.md` and
`proofs/website_studio_ux_20261123/comparison.html`. Production activation remains held.
