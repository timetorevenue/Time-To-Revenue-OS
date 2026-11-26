"""Website Studio code layer and shared editing operations: the server authority.

Python twin of dashboard/frontend/src/studio/live/code.js (schema ttros.studio.code/1). A saved
live-editor version carries element documents plus one optional code layer:
  css     owner CSS appended to app/globals.css in the build (no imports, scripts, remote url())
  header  site header scroll behaviour per breakpoint (desktop / tablet / mobile + threshold)
  files   whole-file source edits of editable code files, applied in the build fixture; a visual
          document with element edits cannot also be overridden
  pages   owner-created landing pages and blog posts (website_studio_pages): settings per new source
          document; their element trees travel with the version's documents
Operations are the one vocabulary for manual inspector edits and Website Manager proposals; this
module validates and applies them to per-document trees so both paths yield identical documents.
Nothing here calls a model, writes the website checkout or publishes.
Revisit: when ops, header modes, editable file rules or the CSS policy change. Last touched: 2026-11-22.
"""
from __future__ import annotations

import copy
import json
import re
from urllib.parse import urlsplit

import website_studio_pages as studio_pages

CODE_SCHEMA = 'ttros.studio.code/1'
HEADER_MODES = ('sticky-transparent', 'sticky-solid', 'auto-hide', 'static')
HEADER_DEFAULT = {'desktop': 'sticky-transparent', 'tablet': 'auto-hide', 'mobile': 'auto-hide', 'threshold': 8}
MAX_CSS = 100_000
MAX_FILE = 300_000
MAX_FILES = 40
BEHAVIOR_FILE = 'components/StudioSiteBehavior.tsx'
EDITABLE_FILE = re.compile(r'(?:(?:app|components|content|lib|styles)/[A-Za-z0-9_\-\[\].]+(?:/[A-Za-z0-9_\-\[\].]+)*\.(?:tsx|ts|jsx|js|mjs|css)|tailwind\.config\.ts)\Z')
BLOCKED_FILES = re.compile(r'(app/api/|middleware\.|next\.config|package(-lock)?\.json|tsconfig|eslint|postcss|vercel|\.)')
URL_IN_CSS = re.compile(r'''url\s*\(\s*(['"]?)([^'")]*)\1\s*\)''', re.I)
OPS = ('set_link', 'set_style', 'set_text', 'set_attr', 'set_class', 'set_css', 'set_header', 'set_file',
       'insert', 'remove', 'create_page', 'create_post', 'set_page', 'remove_page')
PAGE_OPS = ('create_page', 'create_post', 'set_page', 'remove_page')
BPS = ('d', 't', 'm')
MEDIA_ATTRS = {'alt': str, 'title': str, 'aria-label': str, 'loading': ('lazy', 'eager'), 'controls': bool, 'autoPlay': bool,
               'muted': bool, 'loop': bool, 'playsInline': bool, 'preload': ('none', 'metadata', 'auto'), 'src': str, 'poster': str}
# Media sources are checked again as URLs by the document validation (assets, site files, https).
MEDIA_TAGS = ('img', 'Image', 'video', 'audio', 'source', 'iframe')
HOST = re.compile(r'(?=.{4,253}\Z)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z', re.I)


class CodeError(ValueError):
    pass


def file_problem(path, text) -> str | None:
    if (not isinstance(path, str) or not EDITABLE_FILE.fullmatch(path) or '..' in path or '//' in path or BLOCKED_FILES.match(path)
            or re.search(r'(^|/)route\.[jt]sx?\Z', path) or path == BEHAVIOR_FILE):
        return (f'{str(path)[:80]} is not an editable source file (pages, components, content, lib, styles and tailwind.config.ts; '
                'never API routes, config or packages).')
    if text is None:
        return None
    if not isinstance(text, str) or len(text) > MAX_FILE:
        return f'{path} must be text up to {MAX_FILE} characters.'
    if '\x00' in text:
        return f'{path} contains a NUL character.'
    return None


def css_problem(css) -> str | None:
    if not isinstance(css, str):
        return 'CSS must be text.'
    if len(css) > MAX_CSS:
        return f'CSS is limited to {MAX_CSS} characters.'
    if re.search(r'[<\\\x00]', css):
        return 'CSS may not contain <, backslashes or NUL characters.'
    if re.search(r'@import|@charset|expression\s*\(|javascript:|vbscript:|behavior\s*:|-moz-binding', css, re.I):
        return 'CSS may not import, script or bind behaviour.'
    found = list(URL_IN_CSS.finditer(css))
    for m in found:
        if not re.fullmatch(r'asset:[a-f0-9]{64}', m.group(2)) and not re.fullmatch(r'/(?!/)[A-Za-z0-9_\-./]*', m.group(2)):
            return f'url({m.group(2)[:60]}) is not allowed; use a Studio asset or a site file path.'
    if len(re.findall(r'url\s*\(', css, re.I)) != len(found):
        return 'Malformed url().'
    plain = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'', 'S', re.sub(r'/\*[\s\S]*?\*/', '', css))
    if re.search(r'/\*|["\']', plain):
        return 'Unclosed comment or string.'
    depth = 0
    for c in plain:
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth < 0:
                return 'Unbalanced braces.'
    return 'Unbalanced braces.' if depth else None


def css_assets(css) -> list[str]:
    return re.findall(r'asset:([a-f0-9]{64})', css or '')


def header_problem(h) -> str | None:
    if h is None:
        return None
    if not isinstance(h, dict):
        return 'Invalid header settings.'
    for k in h:
        if k not in ('desktop', 'tablet', 'mobile', 'threshold'):
            return f'Unknown header setting {str(k)[:30]}.'
    for bp in ('desktop', 'tablet', 'mobile'):
        if h.get(bp) not in HEADER_MODES:
            return f'Header {bp} mode must be one of {", ".join(HEADER_MODES)}.'
    t = h.get('threshold')
    if not isinstance(t, int) or isinstance(t, bool) or t < 0 or t > 400:
        return 'Header threshold must be 0–400 px.'
    return None


def code_problem(code) -> str | None:
    if code is None:
        return None
    if not isinstance(code, dict):
        return 'Invalid code layer.'
    for k in code:
        if k not in ('css', 'header', 'files', 'pages'):
            return f'Unknown code layer field {str(k)[:30]}.'
    if 'pages' in code:
        p = studio_pages.pages_problem(code['pages'])
        if p:
            return p
    if 'css' in code:
        p = css_problem(code['css'])
        if p:
            return p
    if 'header' in code:
        p = header_problem(code['header'])
        if p:
            return p
    if 'files' in code:
        files = code['files']
        if not isinstance(files, dict):
            return 'Invalid source files.'
        if len(files) > MAX_FILES:
            return f'At most {MAX_FILES} edited source files.'
        for path, text in files.items():
            p = file_problem(path, text)
            if p:
                return p
            if text is None:
                return f'{path}: removed files are not supported.'
            if path in (code.get('pages') or {}):
                return f'{path} is an owner-created page; edit it visually (or remove the page) instead of overriding its source.'
    return None


def normalize(code) -> dict:
    code = code or {}
    out = {}
    if isinstance(code.get('css'), str) and code['css'].strip():
        out['css'] = code['css']
    if code.get('header'):
        h = code['header']
        out['header'] = {'desktop': h.get('desktop'), 'tablet': h.get('tablet'), 'mobile': h.get('mobile'), 'threshold': h.get('threshold')}
    if code.get('files'):
        out['files'] = {k: code['files'][k] for k in sorted(code['files'])}
    if code.get('pages'):
        out['pages'] = studio_pages.normalize(code['pages'])
    return out


def check(code) -> dict:
    problem = code_problem(code)
    if problem:
        raise CodeError(problem)
    return normalize(code)


# ------------------------------------------------------------------------------ links
def link_problem(href, routes: set | None = None, files: set | None = None, unsafe=None) -> str | None:
    v = href.strip() if isinstance(href, str) else ''
    if not v:
        return 'A link needs a destination.'
    if v != href:
        return 'Remove the spaces around the link.'
    if re.search(r'\s', v):
        return 'A link cannot contain spaces.'
    if unsafe:
        p = unsafe('href', v, 'a')
        if p:
            return p
    if re.fullmatch(r'#[A-Za-z][\w-]{0,80}', v):
        return None
    if re.fullmatch(r'mailto:[^@\s]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(\?.*)?', v):
        return None
    if re.fullmatch(r'tel:\+?[0-9][0-9 ()-]{3,24}', v):
        return None
    if re.match(r'https?://', v, re.I):
        try:
            u = urlsplit(v)
            host = u.hostname or ''
        except ValueError:
            return 'That web address is not valid.'
        if u.username or u.password:
            return 'Links may not carry credentials.'
        if not HOST.fullmatch(host):
            return f'{host or "That address"} is not a valid web host.'
        return None
    if re.match(r'/(?!/)', v):
        path = re.split(r'[?#]', v)[0]
        clean = path.rstrip('/') if len(path) > 1 else path
        if routes is not None and clean not in routes and not (files and path in files):
            return f'There is no page or file at {clean} on this site.'
        return None
    return 'Use a full https:// address, a site path such as /pricing, an #anchor, mailto: or tel:.'


# ------------------------------------------------------------------------------ operations
def _find(tree, ident):
    for item in tree:
        if item.get('props', {}).get('id') == ident:
            return item
        for slot in ('kids', 'alt'):
            kids = item.get('props', {}).get(slot)
            if isinstance(kids, list):
                hit = _find(kids, ident)
                if hit is not None:
                    return hit
    return None


def _locate(trees, ident):
    """(list holding the element, index) for an id anywhere in the trees."""
    def walk(items):
        for i, item in enumerate(items):
            if item.get('props', {}).get('id') == ident:
                return items, i
            for slot in ('kids', 'alt'):
                kids = item.get('props', {}).get(slot)
                if isinstance(kids, list):
                    hit = walk(kids)
                    if hit:
                        return hit
        return None
    for tree in trees.values():
        hit = walk(tree)
        if hit:
            return hit
    return None


def apply_ops(lists: dict, code: dict | None, ops, *, routes=None, files=None, style_problem=None, unsafe=None, site=None) -> tuple[dict, dict]:
    """Apply operations to {name: tree} and the code layer. Raises CodeError on the first invalid one.
    Page operations add/remove created documents in the returned lists (``site``: {'routes', 'posts'}
    of the imported site, so new routes stay unique)."""
    out = copy.deepcopy(lists)
    nxt = copy.deepcopy(code or {})
    if not isinstance(ops, list) or not ops or len(ops) > 40:
        raise CodeError('A change needs 1 to 40 operations.')

    def node(ident):
        for tree in out.values():
            hit = _find(tree, ident)
            if hit is not None:
                return hit
        raise CodeError(f'Element {str(ident)[:60]} is not on this page.')

    for op in ops:
        if not isinstance(op, dict) or op.get('op') not in OPS:
            raise CodeError(f'Unknown operation {str((op or {}).get("op") if isinstance(op, dict) else op)[:30]}.')
        kind = op['op']
        if kind in PAGE_OPS:
            pages = copy.deepcopy(nxt.get('pages') or {})
            known = set(routes or ()) | set((site or {}).get('routes') or ())
            try:
                studio_pages.apply_page_op(op, out, pages, {'routes': known, 'posts': set((site or {}).get('posts') or ())})
            except studio_pages.PageError as exc:
                raise CodeError(str(exc)) from exc
            if pages:
                nxt['pages'] = pages
            else:
                nxt.pop('pages', None)
            continue
        if kind == 'insert':
            item = op.get('node')
            if not isinstance(item, dict) or item.get('type') not in ('El', 'Txt', 'Comp') or not isinstance(item.get('props'), dict):
                raise CodeError('Insert one element (with its children).')
            parent = op.get('parent')
            if parent:
                target = node(parent)
                p = target['props']
                slot = op.get('slot') or 'kids'
                if slot not in ('kids', 'alt') or not isinstance(p.get(slot), list) or target['type'] not in ('El', 'Cond'):
                    raise CodeError('That element cannot hold other elements.')
                items = p[slot]
            else:
                if op.get('doc') not in out:
                    raise CodeError('Name the document to insert into.')
                items = out[op['doc']]
            index = op.get('index')
            index = len(items) if index is None else index
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index <= len(items):
                raise CodeError('Invalid insert position.')
            items.insert(index, copy.deepcopy(item))
            continue
        if kind == 'remove':
            hit = _locate(out, op.get('id'))
            if not hit:
                raise CodeError(f'Element {str(op.get("id"))[:60]} is not on this page.')
            if hit[0][hit[1]].get('type') in ('Rec', 'Data'):
                raise CodeError('Content records cannot be removed.')
            hit[0].pop(hit[1])
            continue
        if kind == 'set_link':
            n = node(op.get('id'))
            p = n['props']
            if n['type'] != 'El' or p.get('tag') not in ('a', 'Link'):
                raise CodeError('Only links and buttons that are links have a destination.')
            if (p.get('x') or {}).get('href'):
                raise CodeError('This link’s destination is computed by the site’s code; change it in Code mode.')
            problem = link_problem(op.get('href'), routes, files, unsafe)
            if problem:
                raise CodeError(problem)
            a = {**(p.get('a') or {}), 'href': op['href']}
            if op.get('newTab') is True:
                a['target'], a['rel'] = '_blank', 'noopener noreferrer'
            elif op.get('newTab') is False and a.get('target') == '_blank':
                a.pop('target', None)
                a.pop('rel', None)
            p['a'] = a
        elif kind == 'set_style':
            n = node(op.get('id'))
            p = n['props']
            if n['type'] in ('Txt', 'Rec'):
                raise CodeError('Select the element around this text to style it.')
            if op.get('bp') not in BPS:
                raise CodeError('Unknown breakpoint.')
            values = op.get('values')
            if not isinstance(values, dict) or not values:
                raise CodeError('No style values.')
            st = copy.deepcopy(p.get('st') or {})
            layer = dict(st.get(op['bp']) or {})
            for k, v in values.items():
                if v is None or v == '':
                    layer.pop(k, None)
                else:
                    problem = style_problem(k, v) if style_problem else None
                    if problem:
                        raise CodeError(problem)
                    layer[k] = str(v)
            if layer:
                st[op['bp']] = layer
            else:
                st.pop(op['bp'], None)
            if st:
                p['st'] = st
            else:
                p.pop('st', None)
        elif kind == 'set_text':
            n = node(op.get('id'))
            if n['type'] != 'Txt':
                raise CodeError('Only text can be rewritten.')
            text = op.get('text')
            if not isinstance(text, str) or not text.strip() or len(text) > 40000:
                raise CodeError('Text must not be empty.')
            n['props']['text'] = text
        elif kind == 'set_attr':
            n = node(op.get('id'))
            p = n['props']
            name = op.get('name')
            rule = MEDIA_ATTRS.get(name) if isinstance(name, str) else None
            if n['type'] != 'El' or rule is None or (name in ('src', 'poster') and p.get('tag') not in MEDIA_TAGS):
                raise CodeError(f'Attribute {str(name)[:30]} cannot be set here.')
            if (p.get('x') or {}).get(name):
                raise CodeError(f'{name} is computed by the site’s code.')
            a = dict(p.get('a') or {})
            value = op.get('value')
            if value is None:
                a.pop(name, None)
            elif (isinstance(rule, tuple) and value not in rule) or (rule is bool and not isinstance(value, bool)) or \
                    (rule is str and (not isinstance(value, str) or len(value) > 400)):
                raise CodeError(f'Invalid {name}.')
            else:
                a[name] = value
            if a:
                p['a'] = a
            else:
                p.pop('a', None)
        elif kind == 'set_class':
            n = node(op.get('id'))
            p = n['props']
            if n['type'] != 'El':
                raise CodeError('Only elements have classes.')
            if (p.get('x') or {}).get('className'):
                raise CodeError('This element’s classes are computed by the site’s code.')
            cls = op.get('cls')
            if not isinstance(cls, str) or len(cls) > 2000 or re.search(r'''[<>"'`{}\\]''', cls):
                raise CodeError('Invalid class list.')
            if cls.strip():
                p['cls'] = cls.strip()
            else:
                p.pop('cls', None)
        elif kind == 'set_css':
            css = op.get('css') if op.get('css') is not None else ''
            problem = css_problem(css)
            if problem:
                raise CodeError(problem)
            if css.strip():
                nxt['css'] = css
            else:
                nxt.pop('css', None)
        elif kind == 'set_header':
            problem = header_problem(op.get('header'))
            if problem:
                raise CodeError(problem)
            if op.get('header'):
                nxt['header'] = dict(op['header'])
            else:
                nxt.pop('header', None)
        elif kind == 'set_file':
            problem = file_problem(op.get('path'), op.get('text'))
            if problem:
                raise CodeError(problem)
            if op.get('path') in (nxt.get('pages') or {}):
                raise CodeError(f'{op["path"]} is an owner-created page; edit it visually instead of overriding its source.')
            files_ = dict(nxt.get('files') or {})
            if op.get('text') is None:
                files_.pop(op['path'], None)
            else:
                files_[op['path']] = op['text']
            if files_:
                nxt['files'] = files_
            else:
                nxt.pop('files', None)
    return out, normalize(nxt)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'))
