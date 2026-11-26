"""Shared draft contract, model-free persistence and mocked David review."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from test_website_studio_stage3 import edits, studio, contrast, SITE, src, poisoned_model, app_for, pytest, subprocess, REPO, json

def test_shared_draft_has_independent_history_and_reducer(src, poisoned_model):
    calls, get_david = poisoned_model
    c = app_for(src, get_david)
    data = edits.initial_data('site')
    op = {'summary':'shared change','edits': [{'section':'SharedHeader','field':'name','text':'Shared review'}]}
    next_data = edits.apply_proposal(data, op, page='site')
    saved = c.post('/api/website/studio/sites/'+SITE+'/drafts?page=site',json={'data':next_data,'base_version':0}).json()
    assert saved['version'] == 1
    assert c.get('/api/website/studio/sites/'+SITE+'/drafts?page=home').json()['latest'] == 0
    assert c.get('/api/website/studio/sites/'+SITE+'/drafts/1?page=site').json()['envelope']['page'] == 'site'
    assert c.post('/api/website/studio/sites/'+SITE+'/drafts?page=site',json={'data':data,'base_version':0}).status_code == 409
    assert calls == []

def test_shared_bad_destinations_and_contrast_rejected():
    data=edits.initial_data('site')
    for field,text in [('destination','javascript:bad'),('logo','/unknown.svg'),('navigation','[{"id":"stable-id","label":"Bad","href":"/missing"}]')]:
        with pytest.raises(ValueError):
            studio.apply_operations(data,{'edits':[{'section':'SharedHeader','field':field,'text':text}]},page='site')
    assert contrast.failures(data)==[]
    bad=studio.apply_operations(data,{'edits':[],'styles':[{'section':'SharedHeader','target':'navigation','values':{'text':'#ffffff'}}]},page='site')
    assert contrast.new_failures(data,bad)

def test_shared_python_browser_parity():
    data=edits.initial_data('site')
    op={'edits':[{'section':'SharedFooter','field':'email','text':'owner@example.com'}],'design':{'h2Size':55}}
    js="import {initialDataFor,applyProposal} from './src/studio/state.js';console.log(JSON.stringify(applyProposal(initialDataFor('site'),"+json.dumps(op)+")))"
    result=subprocess.check_output(['node','--input-type=module','-e',js],cwd=REPO/'dashboard/frontend',text=True)
    assert json.loads(result)==studio.apply_operations(data,op,page='site')

def test_shared_david_context_is_bounded_and_contrast_regressions_refused(src, monkeypatch):
    import types
    from unittest.mock import Mock
    monkeypatch.setitem(sys.modules,'aos_david',types.SimpleNamespace(FAST_MODEL='fast'))
    src.refresh(SITE,reference=False)
    david=Mock();c=app_for(src,lambda:david)
    body={'project':SITE,'page':'site','base_version':0,'request':'Update shared CTA','data':edits.initial_data('site')}
    good={'summary':'Shared CTA','edits':[{'section':'SharedHeader','field':'cta','text':'Plan your automation'}]}
    david._call.return_value={'ok':True,'json':{'action':'reply','reply':json.dumps(good)},'usage':{'total_tokens':0}}
    r=c.post('/api/website/studio/propose',json=body)
    assert r.status_code==200,r.text
    assert r.json()['page']['id']=='site'
    prompt=david._call.call_args[0][0]
    assert 'SharedHeader' in prompt and 'SharedFooter' in prompt
    bad={'summary':'Unreadable','edits':[],'styles':[{'section':'SharedHeader','target':'navigation','values':{'text':'#ffffff'}}]}
    david._call.return_value={'ok':True,'json':{'action':'reply','reply':json.dumps(bad)},'usage':{'total_tokens':0}}
    assert c.post('/api/website/studio/propose',json=body).status_code==422

def test_shared_base_inherits_legacy_home_global_design_without_rewriting(src):
    home=edits.initial_data('home');home['root']['props']['design']['accent']='#123456'
    store=edits.Drafts(src);store.save(SITE,home,0,'owner')
    assert store.base(SITE,'site')['root']['props']['design']['accent']=='#123456'
    assert store.latest(SITE,'site')==0 and store.latest(SITE,'home')==1

def test_shared_contrast_guard_covers_inheriting_pages(src):
    base=edits.initial_data('site')
    next_data=studio.apply_operations(base,{'edits':[],'design':{'muted':'#ffffff'}},page='site')
    assert contrast.failures(next_data)==[]
    assert edits.Drafts(src).new_contrast_failures(SITE,base,next_data,'site')
