"""WorkflowFigure (schema v5) persistence, isolation, property bounds and JS/Python validation parity."""
import copy
import json
import subprocess
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parent))
from test_website_studio_stage15 import src, poisoned_model, SITE, app_for
import website_studio_edits as edits
from website_studio_graphic import DEFAULT,validate_graphic
ROOT=Path(__file__).resolve().parents[1]

def data():
    d=edits.initial_data();d['content'][0]['props']['workflow'][0]['props']['graphic']={**DEFAULT,'width':70,'horizontal':'end','accent':'#527a65','line':'#8f8676','titleScale':115,'mobileOrder':'before'}
    return d

def test_graphic_server_draft_roundtrip_without_model_and_other_pages_unchanged(src,poisoned_model):
    calls,model=poisoned_model;c=app_for(src,model);d=data();base='/api/website/studio/sites/'+SITE
    r=c.post(base+'/drafts',json={'data':d,'base_version':0});assert r.status_code==200,r.text
    restored=c.get(base+'/drafts/1').json()['envelope']['data']
    assert restored==edits.canonical(d)
    assert c.get(base+'/drafts?page=service-ai').json()['latest']==0
    assert calls==[]

def test_graphic_python_browser_parity_and_old_draft_migration():
    value=data();script="import {validateData} from './src/studio/state.js';console.log(JSON.stringify(validateData("+json.dumps(value)+")))"
    result=subprocess.check_output(['node','--input-type=module','-e',script],cwd=ROOT/'dashboard/frontend',text=True)
    assert json.loads(result)==edits.canonical(value)
    assert 'graphic' not in edits.canonical(edits.initial_data())['content'][0]['props']

@pytest.mark.parametrize('patch',[{'width':True},{'width':101},{'accent':'red'},{'line':'url(x)'},{'titleScale':141},{'titleScale':True},{'mobileOrder':'hidden'},{'horizontal':'absolute'},{'extra':'x'},{'signal':'#527a65'},{'source':'asset'}])
def test_bad_graphics_rejected(patch):
    with pytest.raises(ValueError):validate_graphic({**DEFAULT,**patch})

def test_graphic_cannot_be_added_to_other_components():
    value=data();value['content'][2]['props']['graphic']=copy.deepcopy(DEFAULT)
    with pytest.raises(ValueError):edits.canonical(value)
