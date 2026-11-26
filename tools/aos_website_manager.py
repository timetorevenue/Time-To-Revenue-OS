"""Website Manager: the website-level agent behind the Studio's conversation panel.

David stays the company-level operator; the Website Manager handles one website request at a time
inside the Website Studio, as an ordinary Job (aos_jobs kind ``website_manager``):

  1. deterministic  every clause of the request is matched by the rules below and becomes a
                    validated Studio operation (website_studio_code ops). Zero model calls.
  2. model          otherwise the Agent Forge package ``website.manager`` (pinned gpt-6-luna low
                    through ModelPort) interprets the request into the same operations. The package
                    must be released (aos_jobs.refs_release_problem); a candidate package is refused
                    truthfully, never routed elsewhere.
  3. needs code     work that needs real source authoring beyond the operations (new components,
                    logic) is answered as needs_input with the Code-mode path that does it.

Specialist capabilities (each answer names one): ``design`` (styles per breakpoint, layout, effects,
inserting and removing elements), ``content_seo`` (new landing pages and blog posts from templates,
page and post settings: SEO title, description, status, category, tags, social image),
``media`` (images, video files and embeds, alt text, playback) and ``frontend`` (owner CSS, header
behaviour, placing React components written in Code mode). Each is deterministic first; the model
route reads what the rules cannot. Typed and spoken requests arrive as the same text.

The output is never applied here: the Studio validates the operations against the exact documents
the owner sees, shows the proposal (diff + preview) and applies it only on accept. The output is
written as the job's artifact (results/website_manager/<key>.json) with its sha256.

Revisit: when the op vocabulary, the package contract or the Jobs executor changes. Last touched: 2026-11-22.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

AGENT = 'website.manager'
ARTIFACT_DIR = Path('results') / 'website_manager'
FONTS = {
    'heading': 'var(--font-geist), "Helvetica Neue", sans-serif',
    'geist': 'var(--font-geist), "Helvetica Neue", sans-serif',
    'body': 'var(--font-inter), "Helvetica Neue", Arial, sans-serif',
    'inter': 'var(--font-inter), "Helvetica Neue", Arial, sans-serif',
    'display': 'var(--font-inter-display), var(--font-inter), sans-serif',
    'inter display': 'var(--font-inter-display), var(--font-inter), sans-serif',
    'serif': 'Georgia, "Times New Roman", serif',
    'sans-serif': '"Helvetica Neue", Arial, sans-serif',
    'sans serif': '"Helvetica Neue", Arial, sans-serif',
    'monospace': 'ui-monospace, SFMono-Regular, Menlo, monospace',
}
WEIGHTS = {'extra bold': '800', 'extrabold': '800', 'semi bold': '600', 'semibold': '600', 'semi-bold': '600', 'bold': '700',
           'medium': '500', 'regular': '400', 'normal': '400', 'light': '300', 'thin': '200'}
BP_WORDS = (('m', r'mobiles?|phones?|small screens?'), ('t', r'tablets?|ipads?'), ('d', r'desktops?|large screens?|wide screens?'))
URL = re.compile(r'''(https?://[^\s"'<>]+|mailto:[^\s"'<>]+|tel:\+?[\d][\d ()-]{3,24}\d|(?<![\w/])/(?!/)[A-Za-z0-9_\-/#?=&.%]*|(?<![\w])#[A-Za-z][\w-]*)''')
SIZE = r'(\d+(?:\.\d+)?)\s*(px|rem|em|%)?'


class Unmatched(Exception):
    pass


# ------------------------------------------------------------------------------ specialist rules
QUOTED = re.compile(r'["“]([^"“”]{1,400})["”]')
CAPS = {'create_page': 'content_seo', 'create_post': 'content_seo', 'set_page': 'content_seo', 'remove_page': 'content_seo',
        'set_css': 'frontend', 'set_file': 'frontend'}
FRONTEND_WORDS = re.compile(r'\b(?:calculator|javascript|react|script|api|webhook|integration|carousel|slider|tabs?|modal|pop-?up|countdown|'
                            r'interactive|logic|state|quiz|configurator|database|login|search box|filter)\b', re.I)
VIDEO_URL = re.compile(r'https?://(?:www\.)?(?:youtube\.com/watch\?v=([\w-]{6,20})[^\s"]*|youtu\.be/([\w-]{6,20})|youtube\.com/embed/([\w-]{6,20})|'
                       r'vimeo\.com/(\d{4,12})|player\.vimeo\.com/video/(\d{4,12}))', re.I)
SHADOWS = {'soft': '0 8px 24px rgba(17, 19, 21, 0.08)', 'subtle': '0 2px 10px rgba(17, 19, 21, 0.06)', 'strong': '0 18px 48px rgba(17, 19, 21, 0.18)',
           'deep': '0 18px 48px rgba(17, 19, 21, 0.18)'}
STATUS_WORDS = {'published': 'published', 'live': 'published', 'draft': 'draft', 'coming soon': 'coming-soon', 'coming-soon': 'coming-soon'}


def slugify(text: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return slug[:60].rstrip('-') or 'page'


def _quoted(text: str) -> list[str]:
    return [m.group(1).strip() for m in QUOTED.finditer(text)]


def _list(text: str) -> list[str]:
    parts = re.split(r'\s*(?:,|;|\band\b)\s*', text.strip().strip('.'))
    return [p.strip().strip('"“”') for p in parts if p.strip().strip('"“”')][:12]


def _image_ref(text: str, ctx: dict, kinds=('image/',)) -> tuple[str, str] | None:
    """An image/video the owner names: asset:<sha>, a site path, or a library asset by name."""
    m = re.search(r'asset:[a-f0-9]{64}', text)
    if m:
        hit = next((a for a in ctx.get('assets') or [] if a['ref'] == m.group(0)), None)
        return m.group(0), (hit or {}).get('name') or ''
    m = re.search(r'(?<![\w/])(/(?!/)[A-Za-z0-9_\-./]+\.(?:png|jpe?g|gif|webp|avif|svg|mp4|webm))\b', text, re.I)
    if m:
        return m.group(1), m.group(1).rsplit('/', 1)[-1]
    low = text.lower()
    for asset in ctx.get('assets') or []:
        name = str(asset.get('name') or '')
        stem = re.sub(r'\.[a-z0-9]+$', '', name.lower())
        if asset.get('type', '').startswith(kinds) and stem and (name.lower() in low or (len(stem) >= 3 and re.search(rf'\b{re.escape(stem)}\b', low))):
            return asset['ref'], name
    return None


def _embed(url_match) -> tuple[str, str]:
    yt = url_match.group(1) or url_match.group(2) or url_match.group(3)
    if yt:
        return f'https://www.youtube-nocookie.com/embed/{yt}', 'youtube'
    return f'https://player.vimeo.com/video/{url_match.group(4) or url_match.group(5)}', 'vimeo'


def _doc_key(ctx: dict) -> str:
    sel = ctx.get('selected') or {}
    if sel.get('id'):
        return sel['id'].split('_')[0]
    root = (ctx.get('page') or {}).get('root') or {}
    if root.get('main'):
        return root['main'].split('_')[0]
    doc = root.get('doc') or (ctx.get('page') or {}).get('doc') or ''
    m = re.fullmatch(r'content/blog-posts/(.+)\.tsx', doc)
    if m:
        return 'post-' + m.group(1)[:42]
    m = re.fullmatch(r'app/(.+)/page\.tsx', doc)
    return re.sub(r'[^a-z0-9]+', '-', m.group(1)).lower() if m else 'x'


def _place(ctx: dict, node: dict) -> dict:
    """An insert op: inside the selected container, after the selected element, else at the end of
    the page's main content (or the post body)."""
    sel = ctx.get('selected') or {}
    if sel.get('id'):
        if sel.get('container'):
            return {'op': 'insert', 'parent': sel['id'], 'node': node}
        if sel.get('parent'):
            return {'op': 'insert', 'parent': sel['parent'], 'index': (sel['index'] + 1) if isinstance(sel.get('index'), int) else None, 'node': node}
        return {'op': 'insert', 'doc': sel.get('doc'), 'index': (sel['index'] + 1) if isinstance(sel.get('index'), int) else None, 'node': node}
    root = (ctx.get('page') or {}).get('root') or {}
    if root.get('main'):
        return {'op': 'insert', 'parent': root['main'], 'node': node}
    if root.get('doc'):
        return {'op': 'insert', 'doc': root['doc'], 'node': node}
    raise Unmatched('no place to insert: select an element first')


def _block(name: str, ctx: dict, fields: dict) -> dict:
    import secrets
    import sys
    backend = str(Path(__file__).resolve().parents[1] / 'dashboard' / 'backend')
    if backend not in sys.path:
        sys.path.append(backend)
    from website_studio_pages import block
    return block(name, _doc_key(ctx), fields, 'w' + secrets.token_hex(3))


def _settings_op(ctx: dict, values: dict) -> dict:
    page = ctx.get('page') or {}
    if not page.get('settings'):
        raise Unmatched('page settings are only editable on pages created in the Studio')
    return {'op': 'set_page', 'doc': page['doc'], 'values': values}


def interpret_site(request: str, ctx: dict) -> dict | None:
    """Content/SEO, media, structural and frontend requests (whole request). None when not one of these."""
    text = request.strip().rstrip('.!')
    low = text.lower()
    quoted = _quoted(text)
    sel = ctx.get('selected') or {}
    page = ctx.get('page') or {}
    settings = page.get('settings') or {}
    try:
        # New landing page.
        if re.search(r'\b(?:create|make|add|build|start|set up|new)\b.*\b(?:landing\s+)?page\b', low) and not re.search(r'\b(?:blog|post|article)\b', low) and quoted:
            title = quoted[0]
            m = re.search(r'\b(?:at|slug|url|path|address)\s+(?:of\s+)?/?([a-z0-9][a-z0-9-]{0,58}[a-z0-9])\b', text)
            template = 'lead-magnet' if re.search(r'lead[- ]magnet|\bform\b|download', low) else 'blank' if re.search(r'\bblank\b|\bempty\b', low) else 'campaign'
            description = quoted[1] if len(quoted) > 1 else f'{title}: practical help from Example Company.'
            settings_new = {'slug': m.group(1) if m else slugify(title), 'title': title, 'description': description, 'template': template,
                            'status': 'published' if re.search(r'\bpublished\b', low) else 'draft'}
            return _done([{'op': 'create_page', 'settings': settings_new}], 'content_seo', opens=f"created:{settings_new['slug']}")
        # New blog post (with a title; drafting a whole article is the content specialist's).
        if re.search(r'\b(?:create|write|draft|add|start|new)\b.*\b(?:blog\s+post|post|article)\b', low) and quoted:
            title = quoted[0]
            m = re.search(r'\bslug\s+/?([a-z0-9][a-z0-9-]{0,58}[a-z0-9])\b', text)
            cat = re.search(r'\b(?:in|under)\s+(?:the\s+)?(?:category\s+)?["“]?([A-Za-z][A-Za-z &-]{1,40}?)["”]?\s+category\b|\bcategory\s+["“]?([A-Za-z][A-Za-z &-]{1,40}?)["”]?(?=\s*(?:,|;|$|\band\b|\btagged\b|\bwith\b))', text)
            tags = re.search(r'\btag(?:ged|s)?\s+(?:with\s+|as\s+)?(.+?)(?=$|;|\.\s|\bwith\b|\bin the\b|\bcategory\b)', text, re.I)
            about = re.search(r'\babout\s+([^,;"“]+?)(?=$|,|;|\btagged\b|\bin the\b|\bcategory\b|\bwith\b)', text, re.I)
            description = quoted[1] if len(quoted) > 1 else (f'A practical guide to {about.group(1).strip()}.' if about else f'{title}: a practical guide from Example Company.')
            cover = re.search(r'\b(?:cover|featured|hero)\s+image\s+(.+?)(?=$|,|;|\btagged\b|\bin the\b)', text, re.I)
            cover_ref = _image_ref(cover.group(1), ctx) if cover else None
            post = {'slug': m.group(1) if m else slugify(title), 'title': title, 'description': description,
                    'category': ((cat.group(1) or cat.group(2)).strip() if cat else 'Insights'), 'tags': _list(tags.group(1)) if tags else [],
                    'date': ctx.get('today'), 'status': 'published' if re.search(r'\bpublished\b', low) else 'draft'}
            if cover_ref:
                post['coverImage'], post['coverImageAlt'] = cover_ref[0], title
            return _done([{'op': 'create_post', 'settings': post}], 'content_seo', opens=f"created-post:{post['slug']}")
        # Settings of a page or post created in the Studio.
        if settings:
            values = {}
            m = re.search(r'\b(?:seo|meta|page|browser|search)\s+title\b', low)
            if m and quoted:
                values['seoTitle'] = quoted[0]
            elif re.search(r'\b(?:meta\s+|seo\s+|search\s+)?description\b', low) and quoted and re.search(r'\b(?:set|change|update|use|make)\b', low):
                values['description'] = quoted[0]
            elif re.search(r'\bexcerpt\b', low) and quoted and settings.get('kind') == 'post':
                values['excerpt'] = quoted[0]
            m = re.search(r'\bcategory\s+(?:to|as)\s+["“]?([^"”,;]+)', text, re.I)
            if m and settings.get('kind') == 'post':
                values['category'] = m.group(1).strip()
            m = re.search(r'\btag(?:s|ged)?\s+(?:it\s+|this\s+post\s+)?(?:with|to|as)\s+(.+)$', text, re.I)
            if m and settings.get('kind') == 'post':
                values['tags'] = _list(m.group(1))
            m = re.search(r'\b(?:keywords?)\s+(?:to|as)\s+(.+)$', text, re.I)
            if m:
                values['keywords' if settings.get('kind') == 'landing' else 'secondaryKeywords'] = _list(m.group(1))
            m = re.search(r'\b(?:social|share|sharing|og|open graph|cover|featured)\s+image\b(.*)$', text, re.I)
            if m:
                ref = _image_ref(m.group(1), ctx)
                if not ref:
                    raise Unmatched('which image?')
                values['ogImage' if settings.get('kind') == 'landing' else 'coverImage'] = ref[0]
            if re.search(r'\bnoindex\b|\bhide\s+(?:it\s+|this\s+page\s+)?from\s+(?:search|google)', low):
                values['noindex'] = True
            elif re.search(r'\b(?:show|allow)\s+(?:it\s+|this\s+page\s+)?(?:in|on)\s+(?:search|google)|\bindex\s+(?:it|this page)\b', low):
                values['noindex'] = None
            m = re.search(r'\b(?:mark|set|make)\s+(?:this\s+|the\s+)?(?:page|post|status)?\s*(?:as|to)?\s*(published|live|draft|coming[- ]soon)\b', low)
            if m:
                values['status'] = STATUS_WORDS[m.group(1).replace('-', ' ') if m.group(1).startswith('coming') else m.group(1)]
            m = re.search(r'\brelated?\s+(?:posts?\s+)?(?:to\s+)?(?:it\s+)?(?:to\s+)?([a-z0-9][a-z0-9, -]+)$', low)
            if m and settings.get('kind') == 'post' and re.search(r'\brelate', low):
                values['relatedPosts'] = [slugify(v) for v in _list(m.group(1))]
            if values:
                return _done([_settings_op(ctx, values)], 'content_seo')
        # Media: video embed by URL.
        vid = VIDEO_URL.search(text)
        if vid and re.search(r'\b(?:add|insert|embed|put|place|show)\b', low):
            src, host = _embed(vid)
            return _done([_place(ctx, _block('youtube', ctx, {'src': src, 'title': quoted[0] if quoted else f'Video ({host})'}))], 'media')
        # Media: a video file.
        m = re.search(r'\b(?:add|insert|put|place)\s+(?:a\s+|an\s+|the\s+|this\s+)?video\b(.*)$', text, re.I)
        if m:
            ref = _image_ref(m.group(1), ctx, kinds=('video/',))
            if not ref:
                raise Unmatched('which video?')
            return _done([_place(ctx, _block('video', ctx, {'src': ref[0], 'title': quoted[0] if quoted else ref[1]}))], 'media')
        # Media: replace the selected image/video, alt text, playback.
        tag = sel.get('tag')
        if tag in ('img', 'Image', 'video'):
            m = re.search(r'\b(?:replace|swap|change)\s+(?:this|the)\s+(?:image|photo|picture|video|logo)\s+(?:with|for|to)\s+(.+)$|\buse\s+(.+?)\s+(?:for|as|in)\s+this\s+(?:image|photo|picture|video)\b', text, re.I)
            if m:
                ref = _image_ref(m.group(1) or m.group(2), ctx, kinds=('image/',) if tag != 'video' else ('video/',))
                if not ref:
                    raise Unmatched('which file?')
                ops = [{'op': 'set_attr', 'id': sel['id'], 'name': 'src', 'value': ref[0]}]
                return _done(ops, 'media')
            m = re.search(r'\balt(?:ernative)?(?:[- ]text)?\b', low)
            if m and quoted and tag != 'video':
                return _done([{'op': 'set_attr', 'id': sel['id'], 'name': 'alt', 'value': quoted[0]}], 'media')
            if tag == 'video':
                ops = []
                for word, name in (('autoplay', 'autoPlay'), ('auto-play', 'autoPlay'), ('loop', 'loop'), ('mute', 'muted'), ('controls', 'controls')):
                    if word in low:
                        off = re.search(rf'\b(?:no|without|turn off|disable|hide|remove)\s+(?:the\s+)?{word}', low) or (word == 'mute' and 'unmute' in low)
                        ops.append({'op': 'set_attr', 'id': sel['id'], 'name': name, 'value': not off})
                if any(o['name'] == 'autoPlay' and o['value'] for o in ops) and not any(o['name'] == 'muted' for o in ops):
                    ops.append({'op': 'set_attr', 'id': sel['id'], 'name': 'muted', 'value': True})  # browsers only autoplay muted video
                if any(o['name'] == 'autoPlay' and o['value'] for o in ops):
                    ops.append({'op': 'set_attr', 'id': sel['id'], 'name': 'playsInline', 'value': True})
                if ops:
                    return _done(ops, 'media')
        # Media: insert an image.
        m = re.search(r'\b(?:add|insert|put|place)\s+(?:a\s+|an\s+|the\s+|this\s+|our\s+)?(?:image|photo|picture|logo|graphic)\b(.*)$', text, re.I)
        if m:
            ref = _image_ref(m.group(1), ctx)
            if not ref:
                raise Unmatched('which image?')
            alt = next((q for q in quoted), None) or re.sub(r'\.[a-z0-9]+$', '', ref[1]).replace('-', ' ').replace('_', ' ').strip() or 'Image'
            return _done([_place(ctx, _block('image', ctx, {'src': ref[0], 'alt': alt}))], 'media')
        # Structure: buttons, links, headings, paragraphs; remove.
        m = re.search(r'\b(?:add|insert|put|place)\s+(?:a\s+|another\s+)?(button|link)\b', low)
        if m and quoted:
            url = URL.search(text.replace(quoted[0], ''))
            if not url:
                raise Unmatched('button destination')
            href = url.group(1).rstrip('.,;:!)')
            return _done([_place(ctx, _block('button' if m.group(1) == 'button' else 'link', ctx, {'href': href, 'label': quoted[0]}))], 'design')
        m = re.search(r'\b(?:add|insert|put)\s+(?:a\s+|another\s+)?(heading|title|subheading|paragraph|text)\b', low)
        if m and quoted:
            return _done([_place(ctx, _block('heading' if m.group(1) in ('heading', 'title', 'subheading') else 'paragraph', ctx, {'text': quoted[0]}))], 'design')
        if re.search(r'^(?:please\s+)?(?:remove|delete)\s+(?:this|the selected|that)\b', low) and sel.get('id'):
            target = sel.get('style_target') if sel.get('type') == 'Txt' else sel['id']
            return _done([{'op': 'remove', 'id': target}], 'design')
        # Frontend: place a component written in Code mode (or one of the site's).
        m = re.search(r'\b(?:place|add|insert|put|use)\s+(?:the\s+|a\s+|my\s+)?([A-Z][A-Za-z0-9]{2,60})\s+component\b', text)
        if m:
            name = m.group(1)
            file = next((f for f in ctx.get('components') or [] if re.search(rf'/{re.escape(name)}\.(?:tsx|jsx)$', f)), None)
            if not file:
                return _needs(f'There is no {name} component yet. Write it in Code mode (components/{name}.tsx), then ask again to place it.',
                              [f'components/{name}.tsx written in Code mode'], 'frontend')
            import secrets
            node = {'type': 'Comp', 'props': {'id': f'{_doc_key(ctx)}_w{secrets.token_hex(3)}', 'name': name, 'file': file}}
            return _done([_place(ctx, node)], 'frontend')
        if FRONTEND_WORDS.search(low) and re.search(r'\b(?:add|build|create|make|need|want)\b', low):
            return _needs('This needs new React code, not a design change. Write the component in Code mode (components/YourComponent.tsx; '
                          'the build type-checks it), then ask me to "place the YourComponent component" here. Owner CSS and header behaviour '
                          'I can change directly.', ['a component written in Code mode'], 'frontend')
        # Effects.
        ops = []
        target = sel.get('style_target') or sel.get('id')
        bps = _bps(low, ctx.get('breakpoint') or 'd')
        m = re.search(r'\b(soft|subtle|strong|deep)?\s*(?:drop\s+)?shadow\b', low)
        if m and re.search(r'\b(?:add|give|put|use|remove|no)\b', low):
            ops += _style(target, bps, {'boxShadow': 'none' if re.search(r'\b(?:remove|no)\b', low) else SHADOWS[m.group(1) or 'soft']})
        if 'gradient' in low:
            named = []
            for k, v in (ctx.get('brand') or {}).get('colors', {}).items():
                hit = re.search(rf'\b{re.escape(k.replace("-", " "))}\b|\b{re.escape(k)}\b', low)
                if hit:
                    named.append((hit.start(), v))
            colors = re.findall(r'#[0-9a-f]{6}\b', text, re.I) or [v for _, v in sorted(named)]  # in the order the owner named them
            if len(colors) < 2:
                raise Unmatched('gradient colours')
            ops += _style(target, bps, {'backgroundImage': f'linear-gradient(135deg, {colors[0]}, {colors[1]})'})
        m = re.search(r'\b(?:opacity|transparen\w*|fade\w*)\b.*?(\d{1,3})\s*%', low)
        if m:
            ops += _style(target, bps, {'opacity': str(round(int(m.group(1)) / 100, 2))})
        if re.search(r'\bhover\b', low) and re.search(r'\b(?:lift|raise|rise|grow|effect|animate|animation|move up)\b', low):
            if not target:
                raise Unmatched('no element selected')
            ops += _style(target, ['d'], {'transition': 'transform 0.2s ease, box-shadow 0.2s ease'})
            css = ((ctx.get('code') or {}).get('css') or '').rstrip()
            rule = f'.s-{target}:hover{{transform:translateY(-3px);box-shadow:0 14px 32px rgba(17,19,21,.14)}}'
            if rule not in css:
                ops.append({'op': 'set_css', 'css': (css + '\n' + rule).strip() + '\n'})
        if re.search(r'\b(?:blur|frosted)\b', low):
            ops += _style(target, bps, {'backdropFilter': 'blur(12px)'})
        if ops:
            merged = []
            for op in ops:
                same = next((x for x in merged if op['op'] == 'set_style' and x['op'] == 'set_style' and x['id'] == op['id'] and x['bp'] == op['bp']), None)
                if same:
                    same['values'].update(op['values'])
                else:
                    merged.append(op)
            return _done(merged, 'design')
    except Unmatched:
        return None
    return None


def _done(ops: list, capability: str, opens: str | None = None) -> dict:
    out = {'route': 'deterministic', 'ops': ops, 'reply': describe(ops), 'model_calls': 0, 'capability': capability}
    if opens:
        out['opens'] = opens
    return out


def _needs(reply: str, missing: list, capability: str) -> dict:
    return {'route': 'deterministic', 'ops': [], 'needs_input': True, 'missing': missing, 'reply': reply, 'model_calls': 0, 'capability': capability}


def capability_of(ops: list) -> str:
    caps = {CAPS.get(op.get('op')) or ('media' if op.get('op') == 'set_attr' and op.get('name') in ('src', 'poster', 'alt', 'autoPlay', 'muted', 'loop', 'controls', 'playsInline', 'preload')
                                       else 'design') for op in ops}
    for name in ('content_seo', 'frontend', 'media', 'design'):
        if name in caps:
            return name
    return 'design'


def _bps(clause: str, default: str) -> list[str]:
    found = [bp for bp, words in BP_WORDS if re.search(rf'\b(?:{words})\b', clause, re.I)]
    return found or [default]


def _unit(number: str, unit: str | None, default: str = 'px') -> str:
    return f'{number}{unit or default}'


def _color(text: str, brand: dict) -> str | None:
    m = re.search(r'#[0-9a-f]{6}\b|#[0-9a-f]{3}\b', text, re.I)
    if m:
        return m.group(0)
    for name, value in sorted((brand.get('colors') or {}).items(), key=lambda kv: -len(kv[0])):
        if re.search(rf'\b{re.escape(name.replace("-", " "))}\b|\b{re.escape(name)}\b', text, re.I):
            return value
    return None


def _style(target: str | None, bps: list[str], values: dict) -> list[dict]:
    if not target:
        raise Unmatched('no element selected')
    return [{'op': 'set_style', 'id': target, 'bp': bp, 'values': dict(values)} for bp in bps]


def _clause(clause: str, ctx: dict) -> list[dict]:
    """Operations for one clause, or Unmatched."""
    c = clause.strip().rstrip('.!')
    low = c.lower()
    sel = ctx.get('selected') or {}
    target = sel.get('id')
    bps = _bps(low, ctx.get('breakpoint') or 'd')
    brand = ctx.get('brand') or {}
    # Owner CSS given explicitly.
    m = re.match(r'(?:please\s+)?(?:add|apply|append|use)\s+(?:this\s+|the\s+following\s+)?(?:custom\s+)?css\s*:?\s*([\s\S]+)$', c, re.I)
    if m:
        css = m.group(1).strip().strip('`').strip()
        existing = (ctx.get('code') or {}).get('css') or ''
        return [{'op': 'set_css', 'css': (existing.rstrip() + '\n' + css).strip() + '\n'}]
    # Header scroll behaviour.
    if re.search(r'\bheader\b|\bnav(?:igation)? bar\b', low) and re.search(r'auto-?hid|hide on scroll|hides? when|sticky|static|scroll|transparent|solid', low):
        current = dict((ctx.get('code') or {}).get('header') or {'desktop': 'sticky-transparent', 'tablet': 'sticky-transparent', 'mobile': 'sticky-transparent', 'threshold': 8})
        if re.search(r'auto-?hid|hide on scroll|hides? (?:when|on)|reveal|reappear', low):
            mode = 'auto-hide'
        elif re.search(r'\bstatic\b|not sticky|scrolls? away', low):
            mode = 'static'
        elif re.search(r'always solid|solid', low):
            mode = 'sticky-solid'
        elif re.search(r'sticky|transparent', low):
            mode = 'sticky-transparent'
        else:
            raise Unmatched('header mode')
        named = [name for bp, name in (('d', 'desktop'), ('t', 'tablet'), ('m', 'mobile')) if bp in bps] if any(
            re.search(rf'\b(?:{w})\b', low) for _, w in BP_WORDS) else ['desktop', 'tablet', 'mobile']
        for name in named:
            current[name] = mode
        return [{'op': 'set_header', 'header': current}]
    # Link destination (an unusable destination still becomes set_link, so the Studio rejects it by name).
    url = URL.search(c)
    loose = (re.search(r'\b(?:link|url|href|button|cta)\b.*?\b(?:to|at)\s+["“]?([^\s"”]+)["”]?\s*(?:(?:and|in|on)\b.*)?$', c, re.I)
             or re.search(r'\buse\s+["“]?([^\s"”]+)["”]?\s+(?:on|for)\s+(?:this|the|that)\s+(?:button|link|cta)\b', c, re.I))
    if loose and not url:
        url = None
    if (url or loose) and re.search(r'\b(link|url|href|button|cta|point|go(?:es)? to|send|destination|open|use)\b', low):
        href = (url.group(1) if url else loose.group(1)).rstrip('.,;:!)')
        link = sel.get('link_id')
        if not link:
            raise Unmatched('no link selected')
        op = {'op': 'set_link', 'id': link, 'href': href}
        if re.search(r'new (?:tab|window)', low):
            op['newTab'] = True
        elif re.search(r'same (?:tab|window)', low):
            op['newTab'] = False
        return [op]
    # Text replacement.
    m = re.search(r'''(?:change|set|replace|update|rename|make)\s+(?:the\s+|this\s+)?(?:text|wording|copy|label|headline|heading|title|button text)\s+(?:to|with|say|read)\s+["“'](.+?)["”']\s*$''', c, re.I)
    if m:
        text_id = sel.get('text_id')
        if not text_id:
            raise Unmatched('no single text run selected')
        return [{'op': 'set_text', 'id': text_id, 'text': m.group(1)}]
    ops: list[dict] = []
    # Typography.
    m = re.search(rf'(?:font[- ]size|text size|size)\s*(?:to|=|of|at)?\s*{SIZE}', low) or re.search(rf'\bmake\s+(?:it|this|the\s+\w+)\s+{SIZE}', low)
    if m:
        ops += _style(target, bps, {'fontSize': _unit(m.group(1), m.group(2))})
    fam = re.search(r'\b(?:font|typeface|font family)\b', low)
    if fam:
        for name in sorted(FONTS, key=len, reverse=True):
            if re.search(rf'\b{re.escape(name)}\b', low):
                ops += _style(target, bps, {'fontFamily': FONTS[name]})
                break
    if re.search(r'\b(?:bold|semi-?bold|semi bold|extra ?bold|medium|regular|light|thin)\b', low) and re.search(r'\b(?:make|font|weight|text|it)\b', low):
        for name in sorted(WEIGHTS, key=len, reverse=True):
            if re.search(rf'\b{re.escape(name)}\b', low):
                ops += _style(target, bps, {'fontWeight': WEIGHTS[name]})
                break
    m2 = re.search(r'line[- ]height\s*(?:to|of|=)?\s*(\d+(?:\.\d+)?)\s*(px|rem|%)?', low)
    if m2:
        ops += _style(target, bps, {'lineHeight': m2.group(1) + (m2.group(2) or '')})
    m2 = re.search(r'letter[- ]spacing\s*(?:to|of|=)?\s*(-?\d+(?:\.\d+)?)\s*(px|em|rem)', low)
    if m2:
        ops += _style(target, bps, {'letterSpacing': m2.group(1) + m2.group(2)})
    m2 = re.search(r'\b(cent(?:er|re)|left|right|justif(?:y|ied))(?:[- ]align(?:ed)?)?\b', low)
    if m2 and re.search(r'\balign|\bcent(?:er|re)|justif', low):
        word = m2.group(1)
        ops += _style(target, bps, {'textAlign': 'center' if word.startswith('cent') else 'justify' if word.startswith('justif') else word})
    if re.search(r'\bbackground(?: colou?r)?\b', low):
        color = _color(c, brand)
        if color:
            ops += _style(target, bps, {'backgroundColor': color})
    elif re.search(r'\b(?:text )?colou?r\b', low):
        color = _color(c, brand)
        if color:
            ops += _style(target, bps, {'color': color})
    # Layout.
    cls = ' ' + str(sel.get('classes') or '') + ' '
    if re.search(r'\bstack(?:ed)?\b|\b(?:one|single|1)[- ]column\b|\bvertical(?:ly)?\b', low):
        if re.search(r'\sgrid\s', cls) or (sel.get('styles') or {}).get('d', {}).get('display') == 'grid':
            ops += _style(target, bps, {'gridTemplateColumns': '1fr'})
        elif re.search(r'\s(?:inline-)?flex\s', cls) or (sel.get('styles') or {}).get('d', {}).get('display') in ('flex', 'inline-flex'):
            ops += _style(target, bps, {'flexDirection': 'column'})
        else:
            ops += _style(target, bps, {'display': 'flex', 'flexDirection': 'column'})
    m2 = re.search(r'\b(\d|two|three|four|five|six)\s+columns?\b', low)
    if m2 and not re.search(r'\b(?:one|single|1)[- ]column\b', low):
        n = {'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6}.get(m2.group(1)) or int(m2.group(1))
        ops += _style(target, bps, {'display': 'grid', 'gridTemplateColumns': f'repeat({n}, minmax(0, 1fr))'})
    if re.search(r'\bside by side\b|\bin a row\b|\bhorizontal(?:ly)?\b', low):
        ops += _style(target, bps, {'display': 'flex', 'flexDirection': 'row'})
    if re.search(r'^(?:please\s+)?hide\b|\bhide (?:it|this)\b', low):
        ops += _style(target, bps, {'display': 'none'})
    for prop in ('padding', 'margin'):
        m2 = re.search(rf'\b{prop}(?:[- ](top|bottom|left|right))?\s*(?:to|of|=)?\s*{SIZE}', low)
        if m2:
            name = prop + (m2.group(1).capitalize() if m2.group(1) else '')
            ops += _style(target, bps, {name: _unit(m2.group(2), m2.group(3))})
    m2 = re.search(rf'\bgap\s*(?:to|of|=)?\s*{SIZE}', low)
    if m2:
        ops += _style(target, bps, {'gap': _unit(m2.group(1), m2.group(2))})
    m2 = re.search(rf'\b(?:max(?:imum)?[- ])?width\s*(?:to|of|=)?\s*{SIZE}', low)
    if m2 and not re.search(r'letter|line', low):
        ops += _style(target, bps, {'maxWidth' if 'max' in low else 'width': _unit(m2.group(1), m2.group(2))})
    m2 = re.search(rf'\b(?:rounded|radius|corners?)\b.*?{SIZE}', low)
    if m2:
        ops += _style(target, bps, {'borderRadius': _unit(m2.group(1), m2.group(2))})
    if not ops:
        raise Unmatched(c[:80])
    # One op per (id, bp): merge values.
    merged: dict[tuple, dict] = {}
    for op in ops:
        key = (op['id'], op['bp'])
        merged.setdefault(key, {'op': 'set_style', 'id': op['id'], 'bp': op['bp'], 'values': {}})['values'].update(op['values'])
    return list(merged.values())


def clauses(text: str) -> list[str]:
    if re.match(r'\s*(?:please\s+)?(?:add|apply|append|use)\s+(?:this\s+|the\s+following\s+)?(?:custom\s+)?css', text, re.I):
        return [text.strip()]
    parts = re.split(r'\s*(?:;|\n|(?<=[a-z0-9)%"”])\.\s+|,?\s+and\s+(?=(?:make|set|change|use|stack|hide|center|centre|align|give|put|add|link|point|on)\b))\s*', text.strip(), flags=re.I)
    return [p for p in parts if p and p.strip()]


def interpret(request: str, ctx: dict) -> dict | None:
    """Deterministic interpretation: operations when every clause is understood, else None."""
    if '<' in request or '>' in request:
        return None  # markup is never a Studio operation; the package refuses it in code
    site = interpret_site(request, ctx)
    if site is not None:
        return site
    ops: list[dict] = []
    # One breakpoint named anywhere in the request applies to clauses that name none
    # ("center the text on mobile and make it bold" is all mobile).
    named = [bp for bp, words in BP_WORDS if re.search(rf'\b(?:{words})\b', request, re.I)]
    if len(named) == 1:
        ctx = {**ctx, 'breakpoint': named[0]}
    try:
        for clause in clauses(request):
            ops += _clause(clause, ctx)
    except Unmatched:
        return None
    if not ops:
        return None
    # Style operations on the same element and breakpoint become one operation.
    merged: list[dict] = []
    for op in ops:
        same = next((m for m in merged if op['op'] == 'set_style' and m['op'] == 'set_style' and m['id'] == op['id'] and m['bp'] == op['bp']), None)
        if same:
            same['values'].update(op['values'])
        else:
            merged.append({**op, 'values': dict(op['values'])} if op['op'] == 'set_style' else op)
    return {'route': 'deterministic', 'ops': merged, 'reply': describe(merged), 'model_calls': 0, 'capability': capability_of(merged)}


def describe(ops: list[dict]) -> str:
    parts = []
    names = {'d': 'desktop', 't': 'tablet', 'm': 'mobile'}
    for op in ops:
        if op['op'] == 'set_link':
            parts.append(f"link {op['id']} → {op['href']}" + (' (new tab)' if op.get('newTab') else ''))
        elif op['op'] == 'set_style':
            parts.append(f"{', '.join(f'{k} {v}' for k, v in op['values'].items())} on {op['id']} at {names[op['bp']]}")
        elif op['op'] == 'set_text':
            parts.append(f"text of {op['id']} → “{op['text'][:60]}”")
        elif op['op'] == 'set_header':
            h = op['header']
            parts.append(f"header: desktop {h['desktop']}, tablet {h['tablet']}, mobile {h['mobile']}")
        elif op['op'] == 'set_css':
            parts.append('owner CSS updated')
        elif op['op'] == 'set_attr':
            parts.append(f"{op['name']} of {op['id']} → {str(op['value'])[:60]}")
        elif op['op'] == 'create_page':
            s = op['settings']
            parts.append(f"new {s.get('template', 'campaign')} landing page “{s['title']}” at /{s['slug']} ({s.get('status', 'draft')})")
        elif op['op'] == 'create_post':
            s = op['settings']
            parts.append(f"new blog post “{s['title']}” at /blog/{s['slug']} in {s.get('category')} ({s.get('status', 'draft')})")
        elif op['op'] == 'set_page':
            parts.append('page settings: ' + ', '.join(f'{k} = {v if not isinstance(v, list) else ", ".join(v)}' for k, v in op['values'].items()))
        elif op['op'] == 'insert':
            node = op['node']
            what = node['props'].get('tag') or node['props'].get('name') or node['type']
            parts.append(f"insert <{what}> " + (f"into {op['parent']}" if op.get('parent') else f"into {op.get('doc')}"))
        elif op['op'] == 'remove':
            parts.append(f"remove {op['id']}")
        else:
            parts.append(op['op'])
    return 'Proposed: ' + '; '.join(parts) + '.'


# ------------------------------------------------------------------------------ job
def _write_artifact(root: Path, job_id: str, value: dict) -> dict:
    from aos_job_ids import storage_key
    from aos_queue_storage import durable_replace_text

    relative = ARTIFACT_DIR / f'{storage_key(job_id)}.json'
    text = json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + '\n'
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    durable_replace_text(path, text)
    return {'ref': f'artifact:website_manager/{storage_key(job_id)}.json', 'path': relative.as_posix(), 'job_id': job_id,
            'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}


def run_job(root: Path, job: dict, *, modelport=None, codex_account=None, package_root: Path | None = None) -> dict:
    """Run one Website Manager turn (aos_jobs executor kind ``website_manager``). Never raises for outcomes."""
    from aos_agents.runtime import RunEnv, empty_usage, run_agent
    import aos_jobs

    spec = job['executor']
    payload = {'request': str(spec.get('request') or job.get('request') or '')[:4000], 'context': spec.get('context') or {}}
    result = {'status': 'failed', 'agent': AGENT, 'output': None, 'artifacts': [], 'usage': empty_usage(), 'detail': '', 'failure_class': ''}
    found = interpret(payload['request'], payload['context'])
    if found is not None:
        output = {**found, 'agent': AGENT, 'job_id': job['id']}
        result.update(status='ok', output=output, artifacts=[_write_artifact(root, job['id'], output)],
                      detail=f"deterministic: {len(found['ops'])} operation(s), no model call")
        return result
    unreleased = aos_jobs.refs_release_problem(package_root or root, [AGENT])
    if unreleased:
        result.update(status='refused', failure_class='agent_not_released',
                      detail=f'This request needs the model route and {unreleased}. Simple requests (links, fonts, sizes, '
                             'layout per breakpoint, header modes, explicit CSS) work without it.')
        return result
    # The step journal lives under the package root; it is the job's own only when the two roots are the
    # same (production). An isolated jobs root runs inline, with usage still recorded against the job.
    same_root = package_root is None or Path(package_root).resolve() == Path(root).resolve()
    env = RunEnv(root=package_root or root, job=job['id'] if same_root else '', modelport=modelport, codex_account=codex_account,
                 ledger_root=Path(root), ledger_item=job['id'])
    ran = run_agent(AGENT, payload, env)
    # A Studio turn is single-shot: a clarifying question is this turn's answer (the owner replies in
    # a new turn), so it settles as ok with needs_input set instead of pausing the job.
    status = 'ok' if ran['status'] == 'needs_input' else ran['status']
    result.update(status=status, usage=ran.get('usage') or empty_usage(), failure_class=ran.get('failure_class') or '',
                  detail=ran.get('detail') or '')
    if ran['status'] in ('ok', 'needs_input') and isinstance(ran.get('output'), dict):
        output = {**ran['output'], 'route': 'model', 'agent': ran.get('agent') or AGENT, 'job_id': job['id'],
                  'model_calls': (ran.get('usage') or {}).get('model_calls'), 'capability': capability_of(ran['output'].get('ops') or [])}
        result['output'] = output
        result['artifacts'] = [_write_artifact(root, job['id'], output)]
    return result
