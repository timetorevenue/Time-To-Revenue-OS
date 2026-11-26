"""Deterministic brand-kit extraction, site comparison and Studio brand compliance.

No model access. The brand kit text is untrusted data: it is parsed with fixed patterns
and anything the patterns cannot read is reported as unextracted, never guessed.
Revisit: when the brand kit layout or the Studio design schema changes. Last touched: 2026-11-18.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import re
from pathlib import Path

from website_studio_design import SCHEMA, DEFAULTS

EXTRACTOR = 'deterministic-brand-kit-v1'
HEX = re.compile(r'#[0-9A-Fa-f]{6}\b')
COLOUR_TOKENS = [k for k, v in SCHEMA['globalTokens'].items() if v['type'] == 'colour']
FONT_TOKENS = [k for k, v in SCHEMA['globalTokens'].items() if v['type'] == 'enum' and 'Geist' in v['options']]
# Studio token -> brand type-scale/component range (desktop values; Studio clamps to mobile itself).
RANGE_TOKENS = {'h1Size': ('type_scale', 'h1'), 'h2Size': ('type_scale', 'h2'), 'h3Size': ('type_scale', 'h3'),
                'bodySize': ('type_scale', 'body'), 'buttonRadius': ('components', 'buttonRadius'),
                'sectionSpacing': ('components', 'sectionSpacing')}
LOCAL_SIZE = {'h1': 'h1Size', 'h2': 'h2Size', 'h3': 'h3Size', 'p': 'bodySize'}
# Studio colour role -> brand role. Fixed table; a role the kit lacks is reported, not invented.
ROLE_MAP = {'background': ('light', 'background'), 'text': ('light', 'text'), 'primary': ('light', 'cta'),
            'accent': ('light', 'accent'), 'borderColor': ('light', 'borders'),
            'secondary': ('palette', 'Mist Grey'), 'muted': ('palette', 'Slate Taupe')}
TEXT_ROLES = {'text', 'muted'}
WCAG_AA = 4.5


def pdf_text(raw: bytes) -> tuple[str, int]:
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(raw))
    return '\n'.join(page.extract_text() or '' for page in reader.pages), len(reader.pages)


def _norm(value: str) -> str:
    return value.upper()


def _join(text: str) -> str:
    return re.sub(r'\s*\n\s*', ' ', text).strip()


def _bullets(text: str, start: str, end: str) -> list[str]:
    match = re.search(re.escape(start) + r'\n(.*?)\n' + re.escape(end), text, re.S)
    if not match:
        return []
    items = re.split(r'\n?• ', '\n' + match.group(1))
    return [_join(item) for item in items if _join(item)]


def _range(text: str, pattern: str):
    match = re.search(pattern, text)
    return [int(match.group(1)), int(match.group(2))] if match else None


def extract(text: str) -> dict:
    """Parse the brand kit text into a structured brand record; never interprets."""
    # pypdf versions differ in leading spaces and blank lines; normalise before matching.
    text = '\n'.join(line.strip() for line in text.splitlines() if line.strip())
    missing = []

    def need(name, value):
        if value in (None, [], {}, ''):
            missing.append(name)
        return value

    palette = {}
    for hexv, name, rgb in re.findall(r'(#[0-9A-Fa-f]{6})\n([A-Z][A-Za-z\- ]+)\n\2\n\1\n(\d+, \d+, \d+)', text):
        palette[name] = {'name': name, 'hex': _norm(hexv), 'rgb': rgb}
    uses = text.split('Recommended Colour Uses', 1)[1] if 'Recommended Colour Uses' in text else ''
    for name, row in palette.items():
        match = re.search(r'\n' + re.escape(name) + r'\n([^\n#]+)', uses)
        row['use'] = match.group(1).strip() if match else ''

    def application(label, stop):
        block = re.search(re.escape(label) + r'\n(.*?)\n' + stop, text, re.S)
        pairs = re.findall(r'([A-Za-z]+)\n(#[0-9A-Fa-f]{6})', block.group(1)) if block else []
        return {k.lower(): _norm(v) for k, v in pairs}

    light = application('Light Version', '•')
    dark = application('Dark Version', '•')
    heading = re.search(r'Headings: ([^.\n]+?)\.', text)
    body = re.search(r'Body: ([A-Za-z]+)\.', text)
    accent_font = re.search(r'Accent font: ([^.\n]+)\.', text)
    fonts = {
        'heading': [f.strip() for f in re.split(r' or |,', heading.group(1))] if heading else [],
        'body': [body.group(1)] if body else [],
        'accent': accent_font.group(1).strip() if accent_font else '',
        'recommended_heading': 'Geist' if heading and 'Geist is the recommended' in text else '',
    }
    scale = {}
    for label, key in (('H1', 'h1'), ('H2', 'h2'), ('H3', 'h3'), ('Body large', 'bodyLarge'), ('Body', 'body')):
        match = re.search(r'\n' + label + r'\n(\d+)-(\d+) px\n(\d+)-(\d+) px\n([^\n]+)', text)
        if match:
            scale[key] = {'desktop': [int(match.group(1)), int(match.group(2))],
                          'mobile': [int(match.group(3)), int(match.group(4))], 'usage': match.group(5).strip()}
    components = {
        'buttonRadius': _range(text, r'(\d+)-(\d+) px radius'),
        'sectionSpacing': _range(text, r'(\d+)-(\d+) px vertical section padding on desktop'),
        'sectionSpacingMobile': _range(text, r'desktop; (\d+)-(\d+) px on mobile'),
    }
    tone = re.search(r'Tone of Voice\nUse\nAvoid\n(.*?\.)\n(.*?\.)\nWords: (.*?\.)\nWords: (.*?\.)\n', text, re.S)
    voice = {'use': _join(tone.group(1)), 'avoid': _join(tone.group(2)),
             'words_use': [w.strip(' .') for w in _join(tone.group(3)).split(',')],
             'words_avoid': [w.strip(' .') for w in _join(tone.group(4)).split(',')]} if tone else {}
    headline = re.search(r'Recommended Homepage Pair\nHeadline: ([^\n]+)\nSubheadline: (.*?)\nTagline Options', text, re.S)
    tagline = re.search(r'Best tagline: ([^\n]+)', text)
    direction = re.search(r'Recommended route: ([^.]+)\.', text)
    brand = {
        'extractor': EXTRACTOR,
        'direction': need('direction', direction.group(1).strip() if direction else ''),
        'palette': need('palette', list(palette.values())),
        'light_application': need('light_application', light),
        'dark_application': need('dark_application', dark),
        'fonts': need('fonts', fonts if fonts['heading'] and fonts['body'] else {}),
        'type_scale': need('type_scale', scale),
        'components': need('components', {k: v for k, v in components.items() if v}),
        'ui_treatment': need('ui_treatment', _bullets(text, 'UI Treatment', 'Premium Conversion Principles')),
        'colour_rules': need('colour_rules', _bullets(text, 'Colour Rules', '=== ') or _bullets(text, 'Colour Rules', 'Example Company - Brand Identity Package')),
        'logo_approach': need('logo_approach', _bullets(text, 'Logo Approach', 'Logo Style Characteristics')),
        'voice': need('voice', voice),
        'messaging': need('messaging', {
            'tagline': tagline.group(1).strip() if tagline else '',
            'headline': headline.group(1).strip() if headline else '',
            'subheadline': _join(headline.group(2)) if headline else '',
            'tagline_options': _bullets(text, 'Tagline Options', 'Elevator Pitch'),
        } if headline else {}),
        'always': need('always', _bullets(text, 'Always Do', 'Never Do')),
        'never': need('never', _bullets(text, 'Never Do', 'Example Company - Brand Identity Package')),
        'homepage_flow': _bullets(text, 'Suggested Homepage Flow', 'UI Treatment'),
    }
    brand['unextracted'] = missing
    brand['needs_interpretation'] = bool(missing)
    return brand


def luminance(hexv: str) -> float:
    def channel(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexv[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return round((la + 0.05) / (lb + 0.05), 2)


def rules(brand: dict, exceptions: list[dict] | None = None) -> dict:
    """Enforceable Studio rules from a brand record plus explicitly approved exceptions."""
    colours = {row['hex'] for row in brand.get('palette', [])}
    colours |= set(brand.get('light_application', {}).values()) | set(brand.get('dark_application', {}).values())
    ranges = {}
    for token, (group, key) in RANGE_TOKENS.items():
        value = brand.get(group, {}).get(key)
        value = value.get('desktop') if isinstance(value, dict) else value
        if value:
            ranges[token] = value
    fonts = [f for f in brand.get('fonts', {}).get('heading', []) + brand.get('fonts', {}).get('body', [])
             if f in SCHEMA['globalTokens']['headingFont']['options']]
    gold = [row['hex'] for row in brand.get('palette', []) if row['name'] in ('Champagne Signal', 'Revenue Gold')]
    clay = [row['hex'] for row in brand.get('palette', []) if row['name'] == 'Alert Clay']
    return {'colours': sorted(colours), 'fonts': sorted(set(fonts)), 'ranges': ranges,
            'no_fill': {'colours': gold, 'tokens': ['background', 'secondary'],
                        'reason': 'Gold is a signal detail, not a fill colour for large areas.'},
            'not_core': {'colours': clay, 'tokens': ['primary', 'accent', 'background', 'text'],
                         'reason': 'Use clay only to identify friction or risk, never as a core brand colour.'},
            'exceptions': copy.deepcopy(exceptions or [])}


def _exception(rule_set, token, value):
    return any(e.get('token') == token and str(e.get('value')).upper() == str(value).upper() for e in rule_set.get('exceptions', []))


def token_violations(design: dict, rule_set: dict, prefix: str = '') -> list[dict]:
    out = []
    for token, value in design.items():
        field = SCHEMA['globalTokens'].get(token)
        if not field:
            continue
        if _exception(rule_set, token, value):
            continue
        if field['type'] == 'colour':
            v = str(value).upper()
            if v not in rule_set['colours']:
                out.append({'token': prefix + token, 'value': value, 'rule': 'Colour is not in the approved brand palette.'})
            elif token in rule_set['no_fill']['tokens'] and v in rule_set['no_fill']['colours']:
                out.append({'token': prefix + token, 'value': value, 'rule': rule_set['no_fill']['reason']})
            elif token in rule_set['not_core']['tokens'] and v in rule_set['not_core']['colours']:
                out.append({'token': prefix + token, 'value': value, 'rule': rule_set['not_core']['reason']})
        elif token in FONT_TOKENS and value != 'inherit' and rule_set['fonts'] and value not in rule_set['fonts']:
            out.append({'token': prefix + token, 'value': value, 'rule': f"Brand fonts are {', '.join(rule_set['fonts'])}."})
        elif token in rule_set['ranges']:
            low, high = rule_set['ranges'][token]
            if not low <= value <= high:
                out.append({'token': prefix + token, 'value': value, 'rule': f'Brand range is {low}–{high}px.'})
    return out


def style_violations(section: str, target: str, tag: str, values: dict, rule_set: dict) -> list[dict]:
    mapped = {}
    for key, value in (values or {}).items():
        if value is None:
            continue
        if key in ('background', 'text', 'borderColor'):
            mapped[{'background': 'background', 'text': 'text', 'borderColor': 'borderColor'}[key]] = value
        elif key == 'radius' and tag == 'a':
            mapped['buttonRadius'] = value
        elif key == 'fontSize' and tag in LOCAL_SIZE:
            mapped[LOCAL_SIZE[tag]] = value
    # A local section background is a fill; local text colours follow the text rules.
    return token_violations(mapped, rule_set, prefix=f'{section}.{target}.')


def proposal_violations(proposal: dict, rule_set: dict, target_tags) -> list[dict]:
    """Only the values a proposal introduces are checked; inherited site values are not its fault."""
    out = token_violations(proposal.get('design') or {}, rule_set)
    for op in proposal.get('styles') or []:
        if op.get('values'):
            out += style_violations(op['section'], op['target'], target_tags(op['section']).get(op['target'], ''), op['values'], rule_set)
    return out


def proposed_tokens(brand: dict, current: dict | None = None) -> tuple[dict, list[dict]]:
    """Nearest brand-compliant Studio tokens; unchanged where the site already complies."""
    current = dict(current or DEFAULTS)
    rule_set = rules(brand)
    palette = {row['name']: row['hex'] for row in brand.get('palette', [])}
    light = brand.get('light_application', {})
    tokens, exceptions = dict(current), []
    for token, (source, key) in ROLE_MAP.items():
        value = current[token].upper()
        if value in rule_set['colours']:
            tokens[token] = value.lower()
            continue
        mapped = (light if source == 'light' else palette).get(key)
        if not mapped:
            continue
        background = tokens.get('background', current['background'])
        if token in TEXT_ROLES and contrast(mapped, background) < WCAG_AA <= contrast(value, background):
            exceptions.append({'token': token, 'value': current[token], 'brand_value': mapped,
                               'reason': f"Brand {key} {mapped} has {contrast(mapped, background)}:1 contrast on {background}, below WCAG AA {WCAG_AA}:1; "
                                         f"the site's {current[token]} ({contrast(value, background)}:1) is retained as an accessibility exception."})
            continue
        tokens[token] = mapped.lower()
    for token in FONT_TOKENS:
        if tokens[token] != 'inherit' and rule_set['fonts'] and tokens[token] not in rule_set['fonts']:
            tokens[token] = 'Inter' if token in ('bodyFamily', 'bodyFont', 'buttonFont') else 'Geist'
    for token, (low, high) in rule_set['ranges'].items():
        tokens[token] = min(max(tokens[token], low), high)
    return tokens, exceptions


def _rem(value: str) -> float:
    return round(float(value) * 16, 1)


def site_styles(repo: Path) -> dict:
    """Read the website's actual style sources with fixed patterns; no evaluation."""
    css = (repo / 'app/globals.css').read_text(encoding='utf-8')
    tailwind = (repo / 'tailwind.config.ts').read_text(encoding='utf-8')
    variables = {k: v.upper() for k, v in re.findall(r'--(ink|paper|stone|gold|muted|rule):\s*(#[0-9a-fA-F]{6})', css)}
    sizes = {}
    for tag in ('h1', 'h2', 'h3'):
        m = re.search(r'\.editorial ' + tag + r' \{ font-size: clamp\(([\d.]+)rem, ([\d.]+)vw, ([\d.]+)rem\)', css)
        if m:
            sizes[tag] = [_rem(m.group(1)), _rem(m.group(3))]
    button = re.search(r'\n\.site-button \{(.*?)\}', css, re.S)
    radius = re.search(r'border-radius:\s*(\d+)px', button.group(1)) if button else None
    hover = re.search(r'\.site-button:hover \{ background: (#[0-9a-fA-F]{6})', css)
    focus = re.search(r':focus-visible \{\s*outline: 2px solid (#[0-9a-fA-F]{6})', css)
    arrow = re.search(r'\.editorial-link \.site-arrow \{ color: (#[0-9a-fA-F]{6})', css)
    tw = {k: v.upper() for k, v in re.findall(r'"?([a-z-]+)"?:\s*"(#[0-9A-Fa-f]{6})"', tailwind)}
    fonts = {'heading': 'Geist' if '--font-geist' in css else '', 'body': 'Inter' if '--font-inter' in css else ''}
    return {'variables': variables, 'heading_px': sizes, 'button_radius_px': int(radius.group(1)) if radius else None,
            'button_hover': hover.group(1).upper() if hover else None, 'focus': focus.group(1).upper() if focus else None,
            'link_arrow': arrow.group(1).upper() if arrow else None, 'tailwind_colours': tw, 'fonts': fonts,
            'legacy_button_radius': 'rounded-md (6px)' if 'rounded-md' in css else None}


def conflicts(brand: dict, site: dict, homepage: dict, guidance: list[dict]) -> list[dict]:
    """Differences between the brand kit and the actual website, each with its source."""
    rule_set = rules(brand)
    names = {row['hex']: row['name'] for row in brand.get('palette', [])}
    out = []
    role = {'ink': 'text', 'paper': 'background', 'stone': 'secondary surface', 'gold': 'accent', 'muted': 'muted text', 'rule': 'borders'}
    for var, value in site['variables'].items():
        if value not in rule_set['colours']:
            out.append({'area': 'colour', 'severity': 'conflict', 'site': f'--{var} {value} ({role[var]})', 'site_source': 'app/globals.css',
                        'brand': 'Not in the brand palette', 'note': ''})
    for label, value in (('Button hover', site.get('button_hover')), ('Focus outline', site.get('focus')), ('Link arrow', site.get('link_arrow'))):
        if value and value not in rule_set['colours']:
            out.append({'area': 'colour', 'severity': 'review', 'site': f'{label} {value}', 'site_source': 'app/globals.css',
                        'brand': 'Not in the brand palette',
                        'note': f'Darker than Champagne Signal; contrast on ivory {contrast(value, "#F7F3EA")}:1 versus {contrast("#B89B63", "#F7F3EA")}:1.' if label != 'Button hover' else 'Brand hover/secondary graphite is Soft Graphite #2B2F32.'})
    if site['tailwind_colours']:
        extra = {k: v for k, v in site['tailwind_colours'].items() if v not in rule_set['colours']}
        out.append({'area': 'colour', 'severity': 'consistent' if not extra else 'conflict',
                    'site': f"Tailwind palette ({len(site['tailwind_colours'])} colours)", 'site_source': 'tailwind.config.ts',
                    'brand': 'Matches the brand palette exactly' if not extra else f'Off-palette: {extra}', 'note': 'Used by legacy pages.'})
    for tag, (low, high) in site['heading_px'].items():
        scale = brand.get('type_scale', {}).get(tag)
        if scale and not (scale['desktop'][0] <= high <= scale['desktop'][1] and scale['mobile'][0] <= low <= scale['mobile'][1]):
            out.append({'area': 'typography', 'severity': 'conflict', 'site': f'{tag.upper()} {low:g}–{high:g}px (clamp)', 'site_source': 'app/globals.css',
                        'brand': f"{tag.upper()} desktop {scale['desktop'][0]}–{scale['desktop'][1]}px, mobile {scale['mobile'][0]}–{scale['mobile'][1]}px", 'note': ''})
    fonts = brand.get('fonts', {})
    out.append({'area': 'typography', 'severity': 'consistent' if site['fonts']['heading'] in fonts.get('heading', []) and site['fonts']['body'] in fonts.get('body', []) else 'conflict',
                'site': f"Headings {site['fonts']['heading']}, body {site['fonts']['body']}", 'site_source': 'app/layout.tsx, app/globals.css',
                'brand': f"Headings {' or '.join(fonts.get('heading', []))}, body {', '.join(fonts.get('body', []))}", 'note': ''})
    radius = brand.get('components', {}).get('buttonRadius')
    if radius and site.get('button_radius_px') is not None and not radius[0] <= site['button_radius_px'] <= radius[1]:
        out.append({'area': 'components', 'severity': 'conflict', 'site': f".site-button radius {site['button_radius_px']}px", 'site_source': 'app/globals.css',
                    'brand': f'Buttons {radius[0]}–{radius[1]}px radius', 'note': f"Legacy .btn-primary uses {site.get('legacy_button_radius')}." if site.get('legacy_button_radius') else ''})
    hero = homepage['sections'][0]['texts']
    current = (hero.get('text0', '') + hero.get('text1', '')).strip()
    messaging = brand.get('messaging', {})
    newer = next((g for g in guidance if 'Addendum' in g['path']), None)
    if messaging.get('headline') and messaging['headline'] != current:
        out.append({'area': 'messaging', 'severity': 'superseded', 'site': f'Hero headline “{current}”', 'site_source': 'app/page.tsx',
                    'brand': f"Brand kit headline “{messaging['headline']}” (27 Apr 2026)",
                    'note': f"The 14 Nov 2026 positioning addendum ({newer['path'] if newer else 'strategy docs'}) governs current copy and supersedes conflicting messaging. Visual identity still follows the brand kit."})
    flow = ' '.join(brand.get('homepage_flow', []))
    if 'Assessment' in flow:
        out.append({'area': 'messaging', 'severity': 'superseded', 'site': 'Conversation CTA (“Discuss your business”)', 'site_source': 'app/page.tsx',
                    'brand': 'Brand kit CTA “Request Assessment” and assessment-led homepage flow',
                    'note': 'business_brain:memory/website_and_content.md says not to drift back to assessment-led copy; the addendum puts assessment options downstream.'})
    return out


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
