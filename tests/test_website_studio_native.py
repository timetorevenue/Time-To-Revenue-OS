"""Native slot contract and read-only historical migration, with David strictly mocked."""
import copy,json,subprocess,sys,types,hashlib
from pathlib import Path
from unittest.mock import Mock
import pytest
sys.path.insert(0,str(Path(__file__).parent))
from test_website_studio_stage15 import src,poisoned_model,app_for,SITE
import website_studio as studio
import website_studio_edits as edits
import website_studio_contrast as contrast
from website_studio_nested import walk_items
ROOT=Path(__file__).resolve().parents[1]

def old():return json.loads((ROOT/'proofs/website_studio_usability_20261118/export-home.json').read_text())
def test_legacy_migration_matches_browser_and_preserves_historical_bytes(src,poisoned_model):
    env=old();env['data']['content'][0]['props']['styles']['figure-0']={'radius':11}
    store=edits.Drafts(src);folder=store._dir(SITE);folder.mkdir(parents=True)
    path=folder/'v0001.json';path.write_text(json.dumps({'version':1,'envelope':env}));before=path.read_bytes()
    # Schema v5: the stored v3 record migrates on read (website_studio_migrate); its bytes never change.
    native=edits.canonical(edits.migration.migrate_envelope(env)[0]['data']);child=native['content'][0]['props']['workflow'][0]
    legacy=env['data']['content'][0]['props']
    assert child['props']['titleLine1']==legacy['text6'] and child['props']['titleLine2']==''
    assert {k:child['props']['graphic'][k] for k in ('width','horizontal','vertical')}=={k:legacy['graphic'][k] for k in ('width','horizontal','vertical')}
    assert [u['path'] for u in native['root']['props']['unsupported']]==['Section0-workflow.graphic.source','Section0-workflow.graphic.asset','Section0-workflow.graphic.alt','Section0-workflow.graphic.signal']
    assert native['root']['props']['unsupported'][1]['value']==legacy['graphic']['asset']
    assert child['props']['styles']['section']=={'radius':11}
    assert store.base(SITE)==edits.migration.migrate_envelope(env)[0]['data']
    script="import{validateEnvelope}from'./src/studio/state.js';console.log(JSON.stringify(validateEnvelope("+json.dumps(env)+")))"
    assert json.loads(subprocess.check_output(['node','--input-type=module','-e',script],cwd=ROOT/'dashboard/frontend',text=True))==native
    assert edits.canonical(native)==native
    c=app_for(src,poisoned_model[1]);r=c.post('/api/website/studio/sites/'+SITE+'/drafts',json={'data':native,'base_version':1});assert r.status_code==200,r.text
    assert store.get(SITE,2)['envelope']['version']==5 and store.get(SITE,2)['envelope']['data']==native
    assert path.read_bytes()==before and not poisoned_model[0]

def test_nested_reducer_selection_and_typed_property_parity():
    d=edits.initial_data();identity=d['content'][0]['props']['workflow'][0]['props']['id']
    p={'summary':'Nested changes','edits':[{'section':identity,'field':'titleLine1','text':'Native workflow'}],'styles':[{'section':identity,'target':'section','values':{'radius':8}}],'components':[{'component':identity,'values':{'graphic':{'width':75,'horizontal':'end'}}}]}
    next_data=edits.apply_proposal(d,p)
    script="import{initialData,applyProposal}from'./src/studio/state.js';console.log(JSON.stringify(applyProposal(initialData,"+json.dumps(p)+")))"
    assert json.loads(subprocess.check_output(['node','--input-type=module','-e',script],cwd=ROOT/'dashboard/frontend',text=True))==next_data
    assert studio.validate_selection({'section':identity,'target':'section'},d)
    for mutation in ('empty','duplicate','wrong','id'):
        bad=copy.deepcopy(d);slot=bad['content'][0]['props']['workflow']
        if mutation=='empty':slot.clear()
        elif mutation=='duplicate':slot.append(copy.deepcopy(slot[0]))
        elif mutation=='wrong':slot[0]['type']='Section1'
        else:slot[0]['props']['id']='Section0'
        with pytest.raises(ValueError):edits.canonical(bad)

def test_david_uses_real_child_identity_and_cannot_break_slots_or_access_models_during_manual_work(src,monkeypatch):
    monkeypatch.setitem(sys.modules,'aos_david',types.SimpleNamespace(FAST_MODEL='mock'))
    src.refresh(SITE,reference=False);david=Mock();c=app_for(src,lambda:david);d=edits.initial_data();identity=d['content'][0]['props']['workflow'][0]['props']['id']
    p={'summary':'Nested proposal','edits':[{'section':identity,'field':'titleLine1','text':'A clear workflow'}],'components':[{'component':identity,'values':{'graphic':{'width':75}}}]}
    david._call.return_value={'ok':True,'json':{'action':'reply','reply':json.dumps(p)},'usage':{'total_tokens':0}}
    body={'project':SITE,'page':'home','base_version':0,'data':d,'request':'Edit the selected workflow','selection':{'section':identity,'target':'section'}}
    response=c.post('/api/website/studio/propose',json=body);assert response.status_code==200,response.text
    assert 'nested_slots' in david._call.call_args[0][0] and identity in david._call.call_args[0][0]
    bad={'summary':'Delete child','edits':[],'sections':[{'op':'remove','id':identity}]}
    david._call.return_value={'ok':True,'json':{'action':'reply','reply':json.dumps(bad)},'usage':{'total_tokens':0}}
    assert c.post('/api/website/studio/propose',json=body).status_code==422
