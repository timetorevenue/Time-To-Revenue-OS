"""Schema-driven native slot trees. Pure migration/validation; no model, network or writes."""
import copy
import json
import re
from pathlib import Path
from website_studio_graphic import DEFAULT, validate_graphic
STUDIO=Path(__file__).resolve().parents[1]/'frontend/src/studio'
RULES=json.loads((STUDIO/'nested-schema.json').read_text())
HOME=json.loads((STUDIO/'homepage.json').read_text())
def slots_for(kind):return RULES['slots'].get(kind,{}) if isinstance(kind,str) else {}
def own_texts(kind,texts):
    old={f for slot in slots_for(kind).values() for f in slot.get('legacy',{}).get('fields',{})}
    return [f for f in texts if f not in old]
def nested_types():
    out={}
    for kind,spec in RULES['components'].items():
        source=next(s for s in HOME['sections'] if s['type']==spec['sourceType'])
        old=next(s['legacy'] for s in slots_for(spec['sourceType']).values() if s.get('legacy',{}).get('type')==kind)
        markup=re.search(r'<'+spec['sourceTag']+r'\b[^>]*class="'+spec['sourceClass']+r'"[\s\S]*?</'+spec['sourceTag']+'>',source['html'])[0]
        for f,k in old['fields'].items():markup=markup.replace(f'data-field="{f}"',f'data-field="{k}"')
        out[kind]={'type':kind,**spec,'html':markup,'texts':{k:source['texts'][f] for f,k in old['fields'].items()},'page':'home','nested':True,'links':[],'domIds':[],'min':0,'max':1,'slug':'workflow','originalId':'Section0-workflow'}
    return out
def child_id(parent,slot):
    full=f'{parent}-{slot}'
    if len(full)<=64:return full
    value=2166136261
    for char in full:value=((value^ord(char))*16777619)&0xffffffff
    return f'{parent[:48]}-{value:08x}'
def migrate_item(value,depth=0):
    if depth>RULES['maxDepth']:raise ValueError('Nested component depth exceeded.')
    if not isinstance(value,dict) or not isinstance(value.get('props'),dict):raise ValueError('Invalid component.')
    item=copy.deepcopy(value)
    for key,slot in slots_for(item.get('type')).items():
        old=slot.get('legacy');p=item['props']
        if 'styles' in p and not isinstance(p['styles'],dict):raise ValueError('Invalid component styles.')
        if old and 'graphic' in p and key in p:raise ValueError('Mixed legacy and native graphic data.')
        if key not in p and old and all(f in p for f in old['fields']):
            p[key]=[{'type':old['type'],'props':{'id':child_id(p['id'],key),**{k:p[f] for f,k in old['fields'].items()},'graphic':copy.deepcopy(p.get('graphic',DEFAULT)),'styles':{'section':copy.deepcopy(p['styles'][old['styleTarget']])} if old['styleTarget'] in p.get('styles',{}) else {}}}]
            for f in old['fields']:p.pop(f)
            p.pop('graphic',None)
            if 'styles' in p:p['styles'].pop(old['styleTarget'],None)
        if isinstance(p.get(key),list):p[key]=[migrate_item(c,depth+1) for c in p[key]]
    return item
def walk_items(content,depth=0):
    if depth>RULES['maxDepth']:raise ValueError('Nested component depth exceeded.')
    for item in content:
        yield item
        for key in slots_for(item['type']):
            if isinstance(item['props'].get(key),list):yield from walk_items(item['props'][key],depth+1)
def validate_children(item,seen,validate_styles,depth=0,count=None):
    count=count if count is not None else [0];count[0]+=1
    if depth>RULES['maxDepth'] or count[0]>RULES['maxComponents']:raise ValueError('Nested component budget exceeded.')
    for key,slot in slots_for(item['type']).items():
        values=item['props'].get(key)
        if not isinstance(values,list) or not slot['min']<=len(values)<=slot['max']:raise ValueError(f"{item['type']}.{key} requires exactly {slot['min']} graphic component.")
        children=[]
        for child in values:
            if not isinstance(child,dict) or child.get('type') not in slot['allow'] or child['type'] not in RULES['components'] or not isinstance(child.get('props'),dict):raise ValueError('Unsupported nested component.')
            spec=nested_types()[child['type']];p=child['props'];identity=p.get('id')
            if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{1,63}',identity) or identity in seen:raise ValueError('Invalid or duplicate nested component ID.')
            seen.add(identity)
            if set(p)-{'id','styles',*spec['texts'],*slots_for(child['type']),*(['graphic'] if spec.get('graphic') else [])}:raise ValueError('Unknown nested component field.')
            for f in spec['texts']:
                if not isinstance(p.get(f),str) or len(p[f])>4000:raise ValueError('Invalid nested text.')
            value={'type':child['type'],'props':{'id':identity,**{f:p[f] for f in spec['texts']},**({'graphic':validate_graphic(p.get('graphic'))} if spec.get('graphic') else {}),**{f:p.get(f) for f in slots_for(child['type'])},'styles':validate_styles(p.get('styles',{}),spec)}}
            validate_children(value,seen,validate_styles,depth+1,count);children.append(value)
        item['props'][key]=children
    return item

def edit_destination(content,identity,field):
    item=next((i for i in walk_items(content) if i['props']['id']==identity),None)
    if not item:return None,field
    for key,spec in slots_for(item['type']).items():
        if isinstance(field,str) and field in spec.get('legacy',{}).get('fields',{}):return item['props'][key][0],spec['legacy']['fields'][field]
    return item,field

def style_destination(content,identity,target):
    item=next((i for i in walk_items(content) if i['props']['id']==identity),None)
    if not item:return None,target
    for key,spec in slots_for(item['type']).items():
        if spec.get('legacy',{}).get('styleTarget')==target:return item['props'][key][0],'section'
    return item,target

def apply_component_patches(content,operations):
    if not isinstance(operations,list) or len(operations)>20:raise ValueError('Invalid component operations.')
    for op in operations:
        if not isinstance(op,dict) or set(op)!={'component','values'} or not isinstance(op['values'],dict):raise ValueError('Invalid component operation.')
        item=next((i for i in walk_items(content) if i['props']['id']==op['component']),None)
        spec=RULES['components'].get(item['type']) if item else None
        if not spec or any(k!='graphic' or not spec.get('graphic') for k in op['values']):raise ValueError('Unknown component property.')
        if 'graphic' in op['values']:
            if not isinstance(op['values']['graphic'],dict):raise ValueError('Invalid graphic patch.')
            item['props']['graphic']=validate_graphic({**item['props']['graphic'],**op['values']['graphic']})

def rekey_children(item):
    for key in slots_for(item['type']):
        for index,child in enumerate(item['props'].get(key,[])):
            child['props']['id']=child_id(item['props']['id'],key if not index else f'{key}-{index}');rekey_children(child)
    return item
