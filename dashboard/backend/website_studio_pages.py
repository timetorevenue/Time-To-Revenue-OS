"""Owner-created landing pages and blog posts: the server authority (Python twin of src/studio/live/pages.js).

A Studio version's code layer may carry ``pages``: {document path: settings}. Each entry is a genuinely
new source file of the website, built from a template (src/studio/live/templates.json) and then edited
like any other element document (literal values only):

  landing   app/<slug>/page.tsx            its own route, metadata (title, description, canonical,
                                           robots, Open Graph/Twitter image), WebPage JSON-LD and a
                                           sitemap entry when published and indexable
  post      content/blog-posts/<slug>.tsx  the post body in the existing blog architecture: a record
                                           appended to content/blog.ts (category, tags, keywords,
                                           status, cover image, related posts, pillar), the body
                                           registered in app/blog/[slug]/page.tsx; the blog index,
                                           related posts, JSON-LD and sitemap come from the site's code

The settings and element trees are the same for the editor's manual controls and Website Manager
operations (create_page, create_post, set_page, remove_page, insert, remove). Header and Footer are
the site's shared documents (kit references). Nothing here writes the website checkout, builds or
publishes; the generator (scripts/studio-live/site-pages.mjs) turns settings into source.
Revisit: when the blog architecture, the templates or the settings fields change. Last touched: 2026-11-23.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = json.loads((ROOT / 'dashboard/frontend/src/studio/live/templates.json').read_text())
# Kit references: the site's shared documents placed by owner-created pages. Pure, so they may sit in
# any document; the generator imports the component.
KIT_CODES = {
    'kit~header': {'doc': '*', 'kind': 'use', 'text': '<Header />', 'pure': True, 'preview': '<Header />', 'kit': {'name': 'Header', 'from': '@/components/Header'}},
    'kit~footer': {'doc': '*', 'kind': 'use', 'text': '<Footer />', 'pure': True, 'preview': '<Footer />', 'kit': {'name': 'Footer', 'from': '@/components/Footer'}},
}
SLUG = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?\Z')
DATE = re.compile(r'20\d\d-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\Z')
# Top-level paths the site, Next.js or the Studio already use.
RESERVED = {'api', 'blog', 'resources', 'guides', 'images', 'fonts', 'studio-assets', '_next', 'favicon', 'robots', 'sitemap', 'manifest',
            'icon', 'apple-icon', 'opengraph-image', 'twitter-image', 'not-found', 'index', 'static', 'public', 'app', 'admin', 'studio'}
LANDING_STATUS = ('draft', 'published')
POST_STATUS = ('draft', 'coming-soon', 'published')
TEXT_BAD = re.compile(r'[<>\x00-\x08\x0b\x0c\x0e-\x1f]')
IMAGE_REF = re.compile(r'asset:[a-f0-9]{64}\Z|/(?!/)[A-Za-z0-9_\-./]{1,200}\.(?:png|jpe?g|gif|webp|avif|svg)\Z', re.I)
MAX_PAGES = 60
# field: (kind, max length) — 'text' single line, 'list' of short texts, 'bool', 'image', 'date', 'enum'.
COMMON = {'title': ('text', 120), 'seoTitle': ('text', 70), 'description': ('text', 320)}
LANDING_FIELDS = {**COMMON, 'kind': ('fixed', 0), 'slug': ('fixed', 0), 'template': ('fixed', 0), 'status': ('enum', LANDING_STATUS),
                  'noindex': ('bool', 0), 'ogImage': ('image', 0), 'ogImageAlt': ('text', 160), 'keywords': ('list', 12)}
POST_FIELDS = {**COMMON, 'kind': ('fixed', 0), 'slug': ('fixed', 0), 'template': ('fixed', 0), 'status': ('enum', POST_STATUS),
               'excerpt': ('text', 400), 'date': ('date', 0), 'updatedDate': ('date', 0), 'category': ('text', 60), 'tags': ('list', 12),
               'targetKeyword': ('text', 120), 'secondaryKeywords': ('list', 12), 'readingTime': ('text', 20), 'coverImage': ('image', 0),
               'coverImageAlt': ('text', 160), 'pillar': ('slug', 0), 'relatedPosts': ('slugs', 6)}
REQUIRED = {'landing': ('kind', 'slug', 'template', 'title', 'description', 'status'),
            'post': ('kind', 'slug', 'template', 'title', 'description', 'date', 'category', 'status')}


class PageError(ValueError):
    pass


def landing_doc(slug: str) -> str:
    return f'app/{slug}/page.tsx'


def post_doc(slug: str) -> str:
    return f'content/blog-posts/{slug}.tsx'


def doc_key(doc: str) -> str:
    """Same keys as live-source.mjs docKey (element ids are <key>_<n>)."""
    m = re.fullmatch(r'app/(.+)/page\.tsx', doc)
    if m:
        return re.sub(r'[^a-z0-9]+', '-', re.sub(r'\[([a-z]+)\]', r'\1', m.group(1)), flags=re.I).lower()
    m = re.fullmatch(r'content/blog-posts/(.+)\.tsx', doc)
    if m:
        return 'post-' + m.group(1)[:42]
    return re.sub(r'[^a-z0-9]+', '-', re.sub(r'\.[a-z]+$', '', doc), flags=re.I).lower()[:48]


def route_of(doc: str, settings: dict) -> str:
    return f"/blog/{settings['slug']}" if settings.get('kind') == 'post' else f"/{settings['slug']}"


def doc_for(kind: str, slug: str) -> str:
    return post_doc(slug) if kind == 'post' else landing_doc(slug)


def _field_problem(name: str, rule: tuple, value) -> str | None:
    kind, limit = rule
    if value is None:
        return None
    if kind == 'text':
        if not isinstance(value, str) or len(value) > limit or TEXT_BAD.search(value):
            return f'{name} must be plain text up to {limit} characters (no < or >).'
    elif kind == 'list':
        if not isinstance(value, list) or len(value) > limit or any(not isinstance(v, str) or not v.strip() or len(v) > 60 or TEXT_BAD.search(v) for v in value):
            return f'{name} must be a list of up to {limit} short texts.'
    elif kind == 'bool':
        if not isinstance(value, bool):
            return f'{name} must be true or false.'
    elif kind == 'image':
        if not isinstance(value, str) or not IMAGE_REF.fullmatch(value):
            return f'{name} must be a Studio image asset or an image file of the site (/images/…).'
    elif kind == 'date':
        if not isinstance(value, str) or not DATE.fullmatch(value):
            return f'{name} must be a date (YYYY-MM-DD).'
    elif kind == 'enum':
        if value not in limit:
            return f'{name} must be one of {", ".join(limit)}.'
    elif kind == 'slug':
        if not isinstance(value, str) or not SLUG.fullmatch(value):
            return f'{name} must be a post slug.'
    elif kind == 'slugs':
        if not isinstance(value, list) or len(value) > limit or any(not isinstance(v, str) or not SLUG.fullmatch(v) for v in value):
            return f'{name} must be a list of up to {limit} post slugs.'
    return None


def settings_problem(doc, settings) -> str | None:
    """Shape of one page's settings (context-free; LiveStudio adds routes, posts and assets)."""
    if not isinstance(settings, dict):
        return 'Invalid page settings.'
    kind = settings.get('kind')
    if kind not in ('landing', 'post'):
        return 'A created page is a landing page or a blog post.'
    slug = settings.get('slug')
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        return 'Use a slug of lowercase letters, digits and hyphens (for example spring-offer).'
    if kind == 'landing' and slug in RESERVED:
        return f'/{slug} is reserved by the site.'
    if doc != doc_for(kind, slug):
        return f'{str(doc)[:80]} does not match the {kind} slug {slug}.'
    fields = POST_FIELDS if kind == 'post' else LANDING_FIELDS
    for name in settings:
        if name not in fields:
            return f'Unknown {kind} setting {str(name)[:30]}.'
    for name in REQUIRED[kind]:
        value = settings.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            return f'{kind.capitalize()} {name} is required.'
    if settings['template'] not in (TEMPLATES['post'] if kind == 'post' else TEMPLATES['landing']):
        return f'Unknown {kind} template {str(settings["template"])[:30]}.'
    for name, value in settings.items():
        if fields[name][0] == 'fixed':
            continue
        problem = _field_problem(name, fields[name], value)
        if problem:
            return problem
    if kind == 'post' and slug in (settings.get('relatedPosts') or []):
        return 'A post cannot be related to itself.'
    return None


def pages_problem(pages) -> str | None:
    if pages is None:
        return None
    if not isinstance(pages, dict):
        return 'Invalid created pages.'
    if len(pages) > MAX_PAGES:
        return f'At most {MAX_PAGES} created pages.'
    keys = set()
    for doc, settings in pages.items():
        problem = settings_problem(doc, settings)
        if problem:
            return problem
        key = doc_key(doc)
        if key in keys:
            return f'Two created pages share the id prefix {key}; choose a different slug.'
        keys.add(key)
    return None


def normalize(pages) -> dict:
    out = {}
    for doc in sorted(pages or {}):
        s = pages[doc]
        out[doc] = {k: s[k] for k in sorted(s) if s[k] is not None and s[k] != '' and s[k] != []}
    return out


def context_problem(pages: dict, *, routes: set, data_keys: set, post_keys: set, asset_ids: set, site_files: set, doc_keys: set) -> str | None:
    """Checks against the imported site: routes free, ids free, referenced posts and images exist."""
    created_posts = {s['slug'] for s in pages.values() if s['kind'] == 'post'}
    for doc, s in pages.items():
        route = route_of(doc, s)
        if route in routes:
            return f'{route} already exists on the site.'
        if doc_key(doc) in doc_keys:
            return f'{doc} would reuse the element ids of an existing document; choose another slug.'
        if s['kind'] == 'post' and s['slug'] in data_keys:
            return f'A blog post with the slug {s["slug"]} already exists.'
        for name in ('ogImage', 'coverImage'):
            ref = s.get(name)
            if not ref:
                continue
            if ref.startswith('asset:') and ref[6:] not in asset_ids:
                return f'{name}: unknown Studio asset {ref[6:18]}; add it to the asset library first.'
            if ref.startswith('/') and ref not in site_files:
                return f'{name}: the site has no file {ref}.'
        if s.get('pillar') and s['pillar'] not in data_keys:
            return f'pillar: there is no blog post {s["pillar"]}.'
        for slug in s.get('relatedPosts') or []:
            if slug not in data_keys and slug not in created_posts:
                return f'relatedPosts: there is no blog post {slug}.'
    return None


def assets(pages) -> list[str]:
    out = []
    for s in (pages or {}).values():
        for name in ('ogImage', 'coverImage'):
            if isinstance(s.get(name), str) and s[name].startswith('asset:'):
                out.append(s[name][6:])
    return sorted(set(out))


def routes(pages) -> set:
    return {route_of(doc, s) for doc, s in (pages or {}).items()}


# ------------------------------------------------------------------------------ trees
def _fill(text: str, fields: dict) -> str:
    return re.sub(r'\{\{(title|description)\}\}', lambda m: fields.get(m.group(1)) or '', text)


def instantiate(tree: list, key: str, fields: dict, start: int = 0, prefix: str = 't') -> tuple[list, int]:
    """Template tree → document tree: ids <key>_<prefix><n> in document order, {{fields}} filled."""
    out = copy.deepcopy(tree)
    n = start

    def visit(items):
        nonlocal n
        for item in items:
            item['props']['id'] = f'{key}_{prefix}{n:x}' if prefix else f'{key}_{n:x}'
            n += 1
            if item['type'] == 'Txt':
                item['props']['text'] = _fill(item['props']['text'], fields)
            for slot in ('kids', 'alt'):
                if isinstance(item['props'].get(slot), list):
                    visit(item['props'][slot])
    visit(out)
    return out, n


def _section(section: dict) -> list:
    heading = str(section.get('heading') or '').strip()
    out = []
    if heading:
        out.append({'type': 'El', 'props': {'tag': 'h2', 'cls': 'font-heading font-semibold text-exec-graphite leading-heading tracking-heading mb-5 scroll-mt-24',
                                            'st': {'d': {'fontSize': 'clamp(22px, 2.2vw, 30px)'}}, 'kids': [{'type': 'Txt', 'props': {'text': heading}}]}})
    for para in section.get('paragraphs') or []:
        if str(para).strip():
            out.append({'type': 'El', 'props': {'tag': 'p', 'cls': 'font-body text-soft-graphite leading-relaxed mb-5 text-base',
                                                'kids': [{'type': 'Txt', 'props': {'text': str(para).strip()}}]}})
    bullets = [str(b).strip() for b in section.get('bullets') or [] if str(b).strip()]
    if bullets:
        out.append({'type': 'El', 'props': {'tag': 'ul', 'cls': 'list-disc pl-6 space-y-2 font-body text-soft-graphite leading-relaxed mb-5',
                                            'kids': [{'type': 'El', 'props': {'tag': 'li', 'kids': [{'type': 'Txt', 'props': {'text': b}}]}} for b in bullets]}})
    return [{'type': 'El', 'props': {'tag': 'section', 'kids': out}}] if out else []


def sections_problem(sections) -> str | None:
    if sections is None:
        return None
    if not isinstance(sections, list) or not 1 <= len(sections) <= 12:
        return 'An article has 1 to 12 sections.'
    for s in sections:
        if not isinstance(s, dict) or set(s) - {'heading', 'paragraphs', 'bullets'}:
            return 'A section has a heading, paragraphs and bullets.'
        texts = [s.get('heading') or ''] + list(s.get('paragraphs') or []) + list(s.get('bullets') or [])
        if any(not isinstance(t, str) or len(t) > 3000 for t in texts) or len(texts) > 40:
            return 'Section text must be plain text.'
    return None


def post_tree(key: str, settings: dict, sections=None, cover=None) -> list:
    """Body of a new post: the template, or (from the content specialist) the given sections; an
    optional cover figure first."""
    template = TEMPLATES['post'][settings['template']]['tree']
    fields = {'title': settings['title'], 'description': settings['description']}
    if sections:
        body = [part for s in sections for part in _section(s)]  # the blog header already shows the description
        template = [{'type': 'El', 'props': {'tag': 'div', 'cls': 'space-y-10', 'kids': body}}]
    tree, n = instantiate(template, key, fields)
    if cover:
        figure, _ = instantiate([{'type': 'El', 'props': {'tag': 'figure', 'cls': 'mb-10 rounded-2xl overflow-hidden border border-warm-stone/60', 'kids': [
            {'type': 'El', 'props': {'tag': 'img', 'a': {'src': cover, 'alt': settings.get('coverImageAlt') or settings['title'], 'loading': 'eager'},
                                     'st': {'d': {'width': '100%', 'height': 'auto', 'display': 'block'}}}}]}}], key, fields, n)
        tree = figure + tree
    return tree


def landing_tree(key: str, settings: dict) -> list:
    tree, _ = instantiate(TEMPLATES['landing'][settings['template']]['tree'], key, {'title': settings['title'], 'description': settings['description']})
    return tree


def block(name: str, key: str, fields: dict | None = None, ident=None) -> dict:
    """One insertable block from templates.json (Website Manager inserts), ids <key>_<ident><n>."""
    spec = TEMPLATES['blocks'].get(name)
    if not spec:
        raise PageError(f'Unknown block {name[:30]}.')
    tree, _ = instantiate([spec['tree']], key, {}, 0, ident or 'w')
    item = tree[0]

    def fill(node):
        p = node['props']
        for k, v in list((p.get('a') or {}).items()):
            if isinstance(v, str) and v.startswith('{{') and v.endswith('}}'):
                value = (fields or {}).get(v[2:-2])
                if value is None:
                    p['a'].pop(k)
                else:
                    p['a'][k] = value
        if node['type'] == 'Txt':
            p['text'] = re.sub(r'\{\{([a-z_]+)\}\}', lambda m: str((fields or {}).get(m.group(1)) or ''), p['text'])
        for kid in p.get('kids') or []:
            fill(kid)
    fill(item)
    return item


# ------------------------------------------------------------------------------ editor pages
def studio_pages(pages: dict, model: dict) -> list[dict]:
    """Studio page entries for created pages (the editor's page list and the manager's context)."""
    blog = next((p for p in model.get('pages', []) if p.get('doc') == 'app/blog/[slug]/page.tsx' and p.get('bind')), None)
    out = []
    for doc, s in sorted((pages or {}).items()):
        if s['kind'] == 'landing':
            out.append({'id': f'created:{s["slug"]}', 'route': route_of(doc, s), 'doc': doc, 'bind': {}, 'label': f'{s["title"]} (new)', 'created': 'landing'})
        else:
            bind = {next(iter(blog['bind'])): doc} if blog else {}
            out.append({'id': f'created-post:{s["slug"]}', 'route': route_of(doc, s), 'doc': 'app/blog/[slug]/page.tsx' if blog else doc, 'bind': bind,
                        'label': f'{s["title"]} (new post)', 'created': 'post', 'post_doc': doc, 'record': {k: v for k, v in s.items() if isinstance(v, str)}})
    return out


def apply_page_op(op: dict, out: dict, pages: dict, ctx: dict) -> None:
    """create_page / create_post / set_page / remove_page on {doc: tree} and the pages map (in place)."""
    kind = op['op']
    if kind in ('create_page', 'create_post'):
        settings = {k: v for k, v in (op.get('settings') or {}).items() if v is not None}
        settings['kind'] = 'post' if kind == 'create_post' else 'landing'
        settings.setdefault('template', 'article' if kind == 'create_post' else 'campaign')
        settings.setdefault('status', 'draft')
        slug = settings.get('slug')
        if not isinstance(slug, str) or not SLUG.fullmatch(slug):
            raise PageError('Use a slug of lowercase letters, digits and hyphens (for example spring-offer).')
        doc = doc_for(settings['kind'], slug)
        if doc in pages or doc in out:
            raise PageError(f'{route_of(doc, settings)} already exists in this Studio.')
        if (ctx.get('routes') is not None and route_of(doc, settings) in ctx['routes']) or (settings['kind'] == 'post' and slug in (ctx.get('posts') or set())):
            raise PageError(f'{route_of(doc, settings)} already exists on the site.')
        problem = settings_problem(doc, settings) or sections_problem(op.get('sections'))
        if problem:
            raise PageError(problem)
        key = doc_key(doc)
        pages[doc] = settings
        out[doc] = post_tree(key, settings, op.get('sections'), settings.get('coverImage')) if settings['kind'] == 'post' else landing_tree(key, settings)
    elif kind == 'set_page':
        doc = op.get('doc')
        if doc not in pages:
            raise PageError('Unknown created page.')
        values = op.get('values')
        if not isinstance(values, dict) or not values:
            raise PageError('No page settings to change.')
        settings = dict(pages[doc])
        for name, value in values.items():
            if name in ('kind', 'slug', 'template'):
                raise PageError(f'{name} cannot be changed; create a new page instead.')
            if value is None or value == '' or value == []:
                settings.pop(name, None)
            else:
                settings[name] = value
        problem = settings_problem(doc, settings)
        if problem:
            raise PageError(problem)
        pages[doc] = settings
    elif kind == 'remove_page':
        doc = op.get('doc')
        if doc not in pages:
            raise PageError('Unknown created page.')
        pages.pop(doc)
        out.pop(doc, None)
