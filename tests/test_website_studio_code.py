"""Website Studio code layer + shared operations (website_studio_code) and their integration in the
live store: validator parity with the JS twin (shared fixture), JS/Python operation parity, versions
carrying the code layer, restore, autosave and the visual/source conflict rule. No network, no model."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dashboard/backend'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))

import website_studio_code as code  # noqa: E402
import website_studio_live as live  # noqa: E402
from test_website_studio_live import SITE, studio, edited, HOME  # noqa: E402,F401

CASES = json.loads((ROOT / 'dashboard/frontend/tests/fixtures/studio-code-cases.json').read_text())
HEADER = {'desktop': 'sticky-transparent', 'tablet': 'auto-hide', 'mobile': 'auto-hide', 'threshold': 8}


@pytest.mark.parametrize('css,ok', CASES['css'])
def test_css_policy_matches_the_js_twin(css, ok):
    assert (code.css_problem(css) is None) is ok, code.css_problem(css)


@pytest.mark.parametrize('href,ok', CASES['links'])
def test_link_policy_matches_the_js_twin(href, ok):
    problem = code.link_problem(href, set(CASES['routes']), set(CASES['files_public']), live.url_problem)
    assert (problem is None) is ok, problem


@pytest.mark.parametrize('path,ok', CASES['files'])
def test_editable_file_policy_matches_the_js_twin(path, ok):
    assert (code.file_problem(path, 'x') is None) is ok


def test_header_and_code_layer_shape():
    assert code.header_problem(HEADER) is None
    assert 'mobile' in code.header_problem({**HEADER, 'mobile': 'float'})
    assert 'threshold' in code.header_problem({**HEADER, 'threshold': True})
    assert 'Unknown code layer field' in code.code_problem({'script': 'x'})
    assert code.normalize({'css': '  ', 'files': {}}) == {}
    assert live.style_problem('backgroundImage', 'linear-gradient(180deg, #F7F3EA, #ECE9E2)') is None
    assert live.style_problem('backgroundImage', 'linear-gradient(180deg, url(x), red)')


TREE = [{'type': 'El', 'props': {'id': 'p_1', 'tag': 'a', 'a': {'href': '/pricing'}, 'kids': [{'type': 'Txt', 'props': {'id': 'p_2', 'text': 'Go'}}]}},
        {'type': 'El', 'props': {'id': 'p_3', 'tag': 'div', 'st': {'d': {'color': '#111315'}}, 'kids': []}},
        {'type': 'El', 'props': {'id': 'p_4', 'tag': 'video', 'a': {'src': '/v.mp4'}}}]
OPS = [{'op': 'set_link', 'id': 'p_1', 'href': 'https://cal.example/booking/fit', 'newTab': True},
       {'op': 'set_style', 'id': 'p_3', 'bp': 'm', 'values': {'fontSize': '18px', 'gridTemplateColumns': '1fr'}},
       {'op': 'set_style', 'id': 'p_3', 'bp': 'd', 'values': {'color': None}},
       {'op': 'set_text', 'id': 'p_2', 'text': 'Book a call'},
       {'op': 'set_attr', 'id': 'p_4', 'name': 'controls', 'value': True},
       {'op': 'set_class', 'id': 'p_3', 'cls': 'grid gap-6'},
       {'op': 'set_css', 'css': '.x{color:red}'}, {'op': 'set_header', 'header': HEADER},
       {'op': 'set_file', 'path': 'lib/config.ts', 'text': 'export const X = 1\n'}]


def test_operations_are_identical_in_python_and_javascript():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    script = ("import {applyOps} from './src/studio/live/code.js';"
              f"const r = applyOps({{page: {json.dumps(TREE)}}}, {{}}, {json.dumps(OPS)}, {{routes: new Set(['/pricing'])}});"
              "process.stdout.write(JSON.stringify(r))")
    out = subprocess.run([node, '--input-type=module', '-e', script], cwd=ROOT / 'dashboard/frontend', capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    js = json.loads(out.stdout)
    lists, layer = code.apply_ops({'page': TREE}, {}, OPS, routes={'/pricing'}, style_problem=live.style_problem, unsafe=live.url_problem)
    assert code.canonical(lists) == code.canonical(js['lists'])
    assert code.canonical(layer) == code.canonical(js['code'])
    assert TREE[0]['props']['a'] == {'href': '/pricing'}  # inputs untouched


@pytest.mark.parametrize('ops,message', [
    ([{'op': 'set_link', 'id': 'p_1', 'href': '/nope'}], 'no page or file'),
    ([{'op': 'set_link', 'id': 'p_1', 'href': 'javascript:alert(1)'}], 'Unsafe'),
    ([{'op': 'set_link', 'id': 'x_9', 'href': '/pricing'}], 'not on this page'),
    ([{'op': 'set_style', 'id': 'p_2', 'bp': 'd', 'values': {'color': 'red'}}], 'element around this text'),
    ([{'op': 'set_style', 'id': 'p_3', 'bp': 'x', 'values': {'color': 'red'}}], 'breakpoint'),
    ([{'op': 'set_text', 'id': 'p_2', 'text': '  '}], 'must not be empty'),
    ([{'op': 'set_css', 'css': '</style><script>'}], 'may not contain'),
    ([{'op': 'set_file', 'path': 'app/api/x/route.ts', 'text': ''}], 'not an editable'),
    ([], '1 to 40'),
])
def test_operations_refuse_with_the_reason(ops, message):
    with pytest.raises(code.CodeError, match=message):
        code.apply_ops({'page': TREE}, {}, ops, routes={'/pricing'}, style_problem=live.style_problem, unsafe=live.url_problem)


def test_versions_carry_the_code_layer_and_restore_it(studio):
    layer = {'css': '.hero{letter-spacing:.01em}', 'header': HEADER}
    v1 = studio.save(SITE, {'app/page.tsx': edited()}, 0, 'owner:test', code=layer)
    assert v1['code'] and 'code-layer' in v1['changed']
    assert studio.code(SITE, 1) == layer
    v2 = studio.save(SITE, {'app/page.tsx': edited('Second')}, 1, 'owner:test')           # code omitted: carried forward
    assert v2['code'] == v1['code'] and 'code-layer' not in v2['changed']
    v3 = studio.save(SITE, {'app/page.tsx': edited('Third')}, 2, 'owner:test', code={})   # explicitly cleared
    assert v3['code'] is None and studio.code(SITE, 3) == {}
    restored = studio.restore(SITE, 1, 3, 'owner:test')
    assert studio.code(SITE, restored['version']) == layer
    assert studio.ledger_intact(SITE)


def test_code_layer_refusals_on_save(studio):
    with pytest.raises(live.LiveError, match='import'):
        studio.save(SITE, {'app/page.tsx': edited()}, 0, 'owner:test', code={'css': '@import url(x);'})
    with pytest.raises(live.LiveError, match='Unknown Studio asset'):
        studio.save(SITE, {'app/page.tsx': edited()}, 0, 'owner:test', code={'css': '.x{background:url("asset:' + 'a' * 64 + '")}'})
    # A visual document with element edits cannot also be overridden as source.
    with pytest.raises(live.LiveError, match='visual edits'):
        studio.save(SITE, {'app/page.tsx': edited()}, 0, 'owner:test', code={'files': {'app/page.tsx': '// x'}})
    # An untouched visual document can be (it becomes code-only).
    entry = studio.save(SITE, {'components/Header.tsx': json.loads(json.dumps(studio.model(SITE)['docs']['components/Header.tsx']['tree']))}, 0,
                        'owner:test', code={'files': {'components/Header.tsx': '// replaced\n'}})
    assert studio.code(SITE, entry['version'])['files'] == {'components/Header.tsx': '// replaced\n'}


def test_invalid_links_are_refused_on_save(studio):
    tree = json.loads(json.dumps(HOME))
    tree[1]['props']['kids'].append({'type': 'El', 'props': {'id': 'home_9', 'tag': 'a', 'a': {'href': 'htps//broken'}, 'kids': []}})
    with pytest.raises(live.LiveError, match='Link home_9'):
        studio.save(SITE, {'app/page.tsx': tree}, 0, 'owner:test')
    tree[1]['props']['kids'][-1]['props']['a']['href'] = '/no-such-page'
    with pytest.raises(live.LiveError, match='no page or file'):
        studio.save(SITE, {'app/page.tsx': tree}, 0, 'owner:test')
    tree[1]['props']['kids'][-1]['props']['a']['href'] = '/'
    assert studio.save(SITE, {'app/page.tsx': tree}, 0, 'owner:test')['version'] == 1


def test_autosave_keeps_a_valid_code_layer_only(studio):
    record = studio.autosave(SITE, {'base_version': 0, 'docs': {'app/page.tsx': edited()}, 'code': {'css': '.a{color:red}'}}, 'owner:test')
    assert 'docs' not in record
    assert studio.get_autosave(SITE)['code'] == {'css': '.a{color:red}'}
    with pytest.raises(live.LiveError):
        studio.autosave(SITE, {'base_version': 0, 'docs': {'app/page.tsx': edited()}, 'code': {'css': 'a{'}}, 'owner:test')
