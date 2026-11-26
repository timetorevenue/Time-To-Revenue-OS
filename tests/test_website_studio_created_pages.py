"""Website Studio Stage 2 (Website Manager architecture, Prompt 2): owner-created landing pages and blog posts (website_studio_pages + the code
layer's ``pages``), the new shared operations (insert, remove, create_page, create_post, set_page,
remove_page) with JS parity, Comp nodes, versions carrying created documents, the Website Manager's
specialist rules (content/SEO, media, design, frontend) and panel API, voice transcription gates,
edited assets, reusable sections and the image-provider status. Synthetic snapshot; no network, no
model, no Git writes."""
import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dashboard/backend'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))

import aos_website_manager as wm  # noqa: E402
import website_studio_code as code  # noqa: E402
import website_studio_live as live  # noqa: E402
import website_studio_manager as manager  # noqa: E402
import website_studio_pages as pages  # noqa: E402
from test_website_studio_live import SITE, PNG, HOME, studio  # noqa: E402,F401
from test_website_studio_manager import Identity, Port, page_docs  # noqa: E402

LANDING = 'app/spring-offer/page.tsx'
POST = 'content/blog-posts/clinic-follow-up.tsx'
CREATE = {'op': 'create_page', 'settings': {'slug': 'spring-offer', 'title': 'Spring offer', 'description': 'Automate follow-up before peak season.'}}
CREATE_POST = {'op': 'create_post', 'settings': {'slug': 'clinic-follow-up', 'title': 'Clinic follow-up', 'description': 'Why clinics lose patients.',
                                                 'date': '2026-11-23', 'category': 'Operations', 'tags': ['clinics']},
               'sections': [{'heading': 'The cost', 'paragraphs': ['One.', 'Two.'], 'bullets': ['a', 'b']}]}


@pytest.fixture()
def site(studio):
    """The synthetic snapshot plus the shared Footer document that created pages place."""
    snap = studio.snapshot_dir(SITE)
    model = json.loads((snap / 'model.json').read_text())
    model['docs']['components/Footer.tsx'] = {'tree': [{'type': 'El', 'props': {'id': 'c-footer_0', 'tag': 'footer', 'kids': []}}]}
    # Routes the templates link to (the real site has them).
    model['pages'] += [{'id': 'free-assessment', 'route': '/free-assessment', 'doc': 'app/page.tsx', 'bind': {}}, {'id': 'pricing', 'route': '/pricing', 'doc': 'app/page.tsx', 'bind': {}}]
    (snap / 'model.json').write_text(json.dumps(model))
    studio._cache.clear()
    return studio


def apply(ops, lists=None, layer=None, **kw):
    return code.apply_ops(lists or {'x': []}, layer or {}, ops, routes={'/', '/pricing'}, style_problem=live.style_problem, unsafe=live.url_problem, **kw)


# ------------------------------------------------------------------------------ settings and ops
@pytest.mark.parametrize('settings,problem', [
    ({'slug': 'blog'}, 'reserved'),
    ({'slug': 'Bad Slug'}, 'slug'),
    ({'title': ''}, 'required'),
    ({'ogImage': 'https://evil.example.com/x.png'}, 'Studio image asset'),
    ({'ogImage': '/images/x.exe'}, 'Studio image asset'),
    ({'title': 'A <script>'}, 'plain text'),
    ({'color': 'red'}, 'Unknown landing setting'),
    ({'status': 'live'}, 'status must be one of'),
])
def test_landing_settings_are_validated(settings, problem):
    s = {'kind': 'landing', 'slug': 'spring-offer', 'template': 'campaign', 'title': 'Spring', 'description': 'x', 'status': 'draft', **settings}
    assert problem in (pages.settings_problem(pages.landing_doc(s['slug']) if pages.SLUG.fullmatch(s['slug']) else LANDING, s) or '')


def test_create_page_and_post_build_documents_from_templates():
    lists, layer = apply([CREATE, CREATE_POST])
    assert set(layer['pages']) == {LANDING, POST}
    assert layer['pages'][LANDING]['status'] == 'draft' and layer['pages'][LANDING]['template'] == 'campaign'
    texts = [i['props']['text'] for i in live._walk(lists[LANDING]) if i['type'] == 'Txt']
    assert 'Spring offer' in texts and 'Automate follow-up before peak season.' in texts
    ids = [i['props']['id'] for i in live._walk(lists[LANDING])]
    assert ids[0] == 'spring-offer_t0' and len(ids) == len(set(ids))
    assert lists[LANDING][0]['props']['use'] == 'kit~header' and lists[LANDING][-1]['props']['use'] == 'kit~footer'
    post_texts = [i['props']['text'] for i in live._walk(lists[POST]) if i['type'] == 'Txt']
    assert {'The cost', 'One.', 'a'} <= set(post_texts)
    for doc, tree in lists.items():
        if doc != 'x':
            live.validate_tree(tree, pages.KIT_CODES, doc, {doc, 'components/Header.tsx', 'components/Footer.tsx'})


@pytest.mark.parametrize('ops,message', [
    ([{**CREATE, 'settings': {**CREATE['settings'], 'slug': 'pricing'}}], 'already exists on the site'),
    ([CREATE, CREATE], 'already exists in this Studio'),
    ([{'op': 'create_post', 'settings': {**CREATE_POST['settings'], 'slug': 'taken'}}], 'already exists on the site'),
    ([CREATE, {'op': 'set_page', 'doc': LANDING, 'values': {'slug': 'other'}}], 'cannot be changed'),
    ([{'op': 'set_page', 'doc': LANDING, 'values': {'title': 'x'}}], 'Unknown created page'),
    ([CREATE, {'op': 'set_file', 'path': LANDING, 'text': 'x'}], 'owner-created page'),
    ([{'op': 'create_post', 'settings': CREATE_POST['settings'], 'sections': [{'heading': 'x', 'html': '<b>'}]}], 'heading, paragraphs and bullets'),
    ([{'op': 'remove', 'id': 'nope_1'}], 'not on this page'),
    ([{'op': 'insert', 'doc': 'x', 'node': {'type': 'Code', 'props': {}}}], 'Insert one element'),
])
def test_page_ops_refuse_collisions_and_invalid_changes(ops, message):
    with pytest.raises(code.CodeError, match=message):
        apply(ops, site={'routes': ['/', '/pricing'], 'posts': ['taken']})


def test_set_page_remove_page_insert_remove():
    node = {'type': 'El', 'props': {'id': 'spring-offer_w1', 'tag': 'p', 'kids': [{'type': 'Txt', 'props': {'id': 'spring-offer_w2', 'text': 'Hi'}}]}}
    lists, layer = apply([CREATE, {'op': 'set_page', 'doc': LANDING, 'values': {'status': 'published', 'keywords': ['a'], 'noindex': True}},
                          {'op': 'insert', 'parent': 'spring-offer_t1', 'index': 0, 'node': node}, {'op': 'remove', 'id': 'spring-offer_t2'}])
    s = layer['pages'][LANDING]
    assert s['status'] == 'published' and s['noindex'] is True and s['keywords'] == ['a']
    main = lists[LANDING][1]
    assert main['props']['kids'][0]['props']['id'] == 'spring-offer_w1' and all(k['props']['id'] != 'spring-offer_t2' for k in main['props']['kids'])
    lists2, layer2 = apply([{'op': 'remove_page', 'doc': LANDING}], lists, layer)
    assert LANDING not in lists2 and 'pages' not in layer2


def test_page_operations_are_identical_in_python_and_javascript():
    node_bin = shutil.which('node')
    if not node_bin:
        pytest.skip('node is not installed')
    ops = [CREATE, CREATE_POST, {'op': 'set_page', 'doc': POST, 'values': {'relatedPosts': ['x-post'], 'coverImage': '/images/site-og.png'}},
           {'op': 'insert', 'doc': LANDING, 'index': 1, 'node': {'type': 'Comp', 'props': {'id': 'spring-offer_w9', 'name': 'Calc', 'file': 'components/Calc.tsx'}}},
           {'op': 'remove', 'id': 'spring-offer_t1'}]
    script = ("import {applyOps} from './src/studio/live/code.js';"
              f"const r = applyOps({{x: []}}, {{}}, {json.dumps(ops)}, {{routes: new Set(['/', '/pricing'])}});"
              "process.stdout.write(JSON.stringify(r))")
    out = subprocess.run([node_bin, '--input-type=module', '-e', script], cwd=ROOT / 'dashboard/frontend', capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    lists, layer = apply(ops)
    js = json.loads(out.stdout)
    assert code.canonical(lists) == code.canonical(js['lists']) and code.canonical(layer) == code.canonical(js['code'])


def test_code_layer_validation_of_pages_and_alt_rule():
    _, layer = apply([CREATE])
    assert code.code_problem(layer) is None
    assert 'Unknown landing setting' in code.code_problem({'pages': {LANDING: {**layer['pages'][LANDING], 'script': 'x'}}})
    assert 'does not match' in code.code_problem({'pages': {'app/other/page.tsx': layer['pages'][LANDING]}})
    video = {'type': 'El', 'props': {'id': 'p_1', 'tag': 'video', 'a': {'src': '/v.webm', 'alt': 'x'}, 'kids': []}}
    with pytest.raises(live.LiveError, match='Alternative text belongs to images'):
        live.validate_tree([video], {}, 'd')


# ------------------------------------------------------------------------------ the live store
def test_versions_carry_created_documents_and_drop_removed_ones(site):
    studio = site
    lists, layer = apply([CREATE])
    v1 = studio.save(SITE, {LANDING: lists[LANDING]}, 0, 'owner:test', code=layer)
    assert LANDING in v1['docs'] and studio.documents(SITE, 1)[LANDING] == lists[LANDING]
    assert studio.code(SITE, 1)['pages'][LANDING]['slug'] == 'spring-offer'
    assert '/spring-offer' in studio.link_scope(SITE, layer['pages'])[0]
    v2 = studio.save(SITE, {}, 1, 'owner:test', code={})
    assert LANDING not in v2['docs'] and LANDING not in studio.documents(SITE, 2)
    with pytest.raises(live.LiveError, match='Unknown document'):
        studio.save(SITE, {LANDING: lists[LANDING]}, 2, 'owner:test', code={})
    restored = studio.restore(SITE, 1, 2, 'owner:test')
    assert LANDING in restored['docs'] and studio.code(SITE, 3)['pages'] and studio.ledger_intact(SITE)


def test_created_pages_cannot_take_an_existing_route_or_unknown_images(site):
    studio = site
    lists, layer = apply([{**CREATE, 'settings': {**CREATE['settings'], 'slug': 'home-two'}}])
    bad = json.loads(json.dumps(layer))
    bad['pages']['app/home-two/page.tsx']['ogImage'] = 'asset:' + 'a' * 64
    with pytest.raises(live.LiveError, match='unknown Studio asset'):
        studio.save(SITE, {k: v for k, v in lists.items() if k != 'x'}, 0, 'owner:test', code=bad)
    clash = {'app/page.tsx': {**layer['pages']['app/home-two/page.tsx']}}
    with pytest.raises(live.LiveError):
        studio.save(SITE, {}, 0, 'owner:test', code={'pages': clash})


def test_placed_components_must_exist(site):
    studio = site
    lists, layer = apply([CREATE, {'op': 'insert', 'doc': LANDING, 'node': {'type': 'Comp', 'props': {'id': 'spring-offer_w1', 'name': 'Calc', 'file': 'components/Calc.tsx'}}}])
    with pytest.raises(live.LiveError, match='does not exist'):
        studio.save(SITE, {LANDING: lists[LANDING]}, 0, 'owner:test', code=layer)
    layer = {**layer, 'files': {'components/Calc.tsx': 'export default function Calc() { return null }\n'}}
    assert studio.save(SITE, {LANDING: lists[LANDING]}, 0, 'owner:test', code=layer)['version'] == 1


def test_edited_assets_sections_and_provider_status(site):
    studio = site
    from test_website_studio_live import FakeSources
    parent = studio.add_asset(SITE, PNG, 'logo.png', 'owner:test', {'kind': 'upload'})
    derived = studio.derive_asset(SITE, PNG + b'', 'logo-edited.png', 'owner:test', parent['id'], {'ratio': '1:1'})
    assert derived['provenance']['kind'] in ('studio_edit', 'upload')  # same bytes dedupe to the parent
    with pytest.raises(live.LiveError, match='from the Studio library'):
        studio.derive_asset(SITE, PNG, 'x.png', 'owner:test', 'b' * 64, {})
    row = studio.save_section(SITE, 'Hero', {'type': 'El', 'props': {'id': 'x_1', 'tag': 'section', 'kids': [{'type': 'Txt', 'props': {'id': 'x_2', 'text': 'Hi'}}]}}, 'owner:test')
    assert studio.sections(SITE)[0]['id'] == row['id']
    with pytest.raises(live.LiveError, match='page code'):
        studio.save_section(SITE, 'Bound', {'type': 'El', 'props': {'id': 'x_3', 'tag': 'a', 'x': {'href': 'home~0'}, 'kids': []}}, 'owner:test')
    app = FastAPI()
    app.include_router(live.router(FakeSources(studio.sources.state), Identity(), studio))
    client = TestClient(app, headers={'X-TTROS-Connections': '1'})
    providers = client.get(f'/api/website/studio/sites/{SITE}/live/media/providers').json()
    assert providers['image_generation']['available'] is False and providers['image_editing']['available'] is True


# ------------------------------------------------------------------------------ the Website Manager
CTX = {'today': '2026-11-23', 'breakpoint': 'd', 'brand': {'colors': {'ivory': '#F7F3EA', 'champagne': '#B89B63'}},
       'assets': [{'ref': 'asset:' + 'a' * 64, 'name': 'team.jpg', 'type': 'image/jpeg'}, {'ref': 'asset:' + 'b' * 64, 'name': 'demo.webm', 'type': 'video/webm'}],
       'page': {'id': 'home', 'route': '/', 'root': {'doc': 'app/page.tsx', 'main': 'home_1'}}, 'components': ['components/Calc.tsx']}
SEL = {'id': 'home_9', 'type': 'El', 'tag': 'div', 'parent': 'home_1', 'index': 2, 'container': True, 'style_target': 'home_9', 'doc': 'app/page.tsx'}
CREATED = {**CTX, 'page': {'id': 'created:spring-offer', 'doc': LANDING, 'settings': {'kind': 'landing', 'slug': 'spring-offer'}, 'root': {'doc': LANDING, 'main': 'spring-offer_t1'}}}


@pytest.mark.parametrize('text,ctx,capability,check', [
    ('create a landing page called "Spring offer" at /spring-offer', CTX, 'content_seo', lambda o: o[0]['op'] == 'create_page' and o[0]['settings']['slug'] == 'spring-offer'),
    ('write a blog post titled "Clinic follow-up" in the Operations category tagged clinics, follow-up', CTX, 'content_seo',
     lambda o: o[0]['op'] == 'create_post' and o[0]['settings']['category'] == 'Operations' and o[0]['settings']['tags'] == ['clinics', 'follow-up'] and o[0]['settings']['date'] == '2026-11-23'),
    ('set the SEO title to "Spring | EXCO"', CREATED, 'content_seo', lambda o: o == [{'op': 'set_page', 'doc': LANDING, 'values': {'seoTitle': 'Spring | EXCO'}}]),
    ('mark this page as published', CREATED, 'content_seo', lambda o: o[0]['values'] == {'status': 'published'}),
    ('set the social image to team.jpg', CREATED, 'content_seo', lambda o: o[0]['values'] == {'ogImage': 'asset:' + 'a' * 64}),
    ('add a video https://youtu.be/dQw4w9WgXcQ', {**CTX, 'selected': SEL}, 'media', lambda o: 'youtube-nocookie.com/embed/dQw4w9WgXcQ' in json.dumps(o) and o[0]['parent'] == 'home_9'),
    ('add the video demo.webm', {**CTX, 'selected': SEL}, 'media', lambda o: o[0]['node']['props']['tag'] == 'video'),
    ('add an image team.jpg', CTX, 'media', lambda o: o[0]['parent'] == 'home_1'),
    ('add a button "Book" linking to /pricing', {**CTX, 'selected': {**SEL, 'container': False}}, 'design', lambda o: o[0]['parent'] == 'home_1' and o[0]['index'] == 3),
    ('remove this', {**CTX, 'selected': SEL}, 'design', lambda o: o == [{'op': 'remove', 'id': 'home_9'}]),
    ('add a soft shadow and a hover lift', {**CTX, 'selected': SEL}, 'design', lambda o: any(x['op'] == 'set_css' and '.s-home_9:hover' in x['css'] for x in o)),
    ('give it a gradient background from ivory to champagne', {**CTX, 'selected': SEL}, 'design', lambda o: o[0]['values']['backgroundImage'] == 'linear-gradient(135deg, #F7F3EA, #B89B63)'),
    ('place the Calc component here', {**CTX, 'selected': SEL}, 'frontend', lambda o: o[0]['node']['type'] == 'Comp' and o[0]['node']['props']['file'] == 'components/Calc.tsx'),
])
def test_specialist_rules(text, ctx, capability, check):
    found = wm.interpret(text, ctx)
    assert found and found['route'] == 'deterministic' and found['model_calls'] == 0 and found['capability'] == capability, found
    assert check(found['ops']), found['ops']


def test_frontend_requests_answer_with_the_code_mode_path_and_vague_ones_go_to_the_model():
    found = wm.interpret('build an interactive pricing calculator', {**CTX, 'selected': SEL})
    assert found['needs_input'] and found['capability'] == 'frontend' and not found['ops']
    assert wm.interpret('make this feel more premium', {**CTX, 'selected': SEL}) is None


@pytest.fixture()
def api(site, tmp_path):
    studio = site
    def source_diff(site, docs, code_layer, before=None):
        return {'files': [], 'changed': sorted(d for d in docs if d not in (before or {}).get('docs', {}))}
    studio.source_diff = source_diff
    app = FastAPI()
    app.include_router(manager.router(None, Identity(), studio, jobs_root=tmp_path / 'jobs', package_root=ROOT, modelport=Port()))
    return TestClient(app, headers={'X-TTROS-Connections': '1'})


BASE = f'/api/website/studio/sites/{SITE}/live/manager'


def test_manager_creates_a_post_as_a_proposal_and_records_spoken_input(api):
    body = {'text': 'write a blog post titled "Clinic follow-up" in the Operations category', 'page': 'home', 'selected': None, 'bp': 'd',
            'base_version': 0, 'docs': page_docs(), 'code': {}, 'input': 'voice', 'today': '2026-11-23'}
    r = api.post(BASE + '/turns', json=body).json()
    assert r['outcome'] == 'proposal' and r['input'] == 'voice' and r['capability'] == 'content_seo'
    p = r['proposal']
    assert p['changed_docs'] == [POST] and p['code']['pages'][POST]['date'] == '2026-11-23' and p['input'] == 'voice'
    # The created post's page accepts follow-up turns with its settings in context.
    created_body = {**body, 'text': 'set the category to "Clinics"', 'page': 'created-post:clinic-follow-up', 'docs': {POST: p['docs'][POST]}, 'code': p['code'], 'input': 'typed'}
    r2 = api.post(BASE + '/turns', json=created_body).json()
    assert r2['outcome'] == 'proposal' and r2['proposal']['code']['pages'][POST]['category'] == 'Clinics'


def test_voice_status_and_transcription_gates(api, monkeypatch):
    monkeypatch.setattr(manager, 'STT_PYTHON', Path('/nonexistent/python'))
    assert api.get(BASE + '/voice').json()['server_transcription'] == 'unavailable'
    audio = base64.b64encode(b'\x1aE\xdf\xa3' + b'0' * 100).decode()
    r = api.post(BASE + '/transcribe', json={'audio': audio, 'mime': 'audio/webm'})
    assert r.status_code == 422 and 'not installed' in r.json()['detail']
    assert api.post(BASE + '/transcribe', json={'audio': audio, 'mime': 'video/x-msvideo'}).status_code == 422
    assert api.post(BASE + '/transcribe', json={'audio': audio, 'mime': 'audio/webm'}, headers={'X-TTROS-Connections': ''}).status_code == 403
