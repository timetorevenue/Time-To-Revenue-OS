import { createContext, useContext, useId } from 'react'
import { schema, defaultDesign, elementTargets, localFields } from './design.js'
export const SelectionContext = createContext({ selection:null, select:()=>{} })
// `${instance}:${target}` keys of elements whose text fails WCAG AA contrast (editor canvas only).
export const ContrastContext = createContext(null)
function Input({definition:f,value,onChange,label}) {
 const id=useId()
 return <label className="studio-control" htmlFor={id}><span>{label || f.label}</span>{f.type==='enum'?
  <select id={id} aria-label={label || f.label} value={value} onChange={e=>onChange(e.target.value)}>{f.options.map(v=><option key={v} value={v}>{v==='inherit'?'Use family default':v}</option>)}</select>:
  <input id={id} aria-label={label || f.label} type={f.type==='colour'?'color':'number'} value={value} min={f.min} max={f.max} step={f.step} onChange={e=>{
   const v=f.type==='number'?e.target.valueAsNumber:e.target.value
   if(f.type==='number' && (!Number.isFinite(v)||v<f.min||v>f.max))return
   onChange(v)
  }}/>}</label>
}
export function GlobalDesignField({value=defaultDesign,onChange}) {
 const groups=[...new Set(Object.values(schema.globalTokens).map(f=>f.group))]
 return <div className="studio-design-fields">
  <p className="studio-help">Changes flow to every inheriting element. Select a section or element to override it.</p>
  <div className="studio-palettes" aria-label="Named palettes">{Object.entries(schema.palettes).map(([name,colours])=><button key={name} onClick={()=>onChange({...value,...colours})}><span className="studio-swatches">{['background','primary','accent'].map(k=><i key={k} style={{background:colours[k]}}/>)}</span>{name}</button>)}</div>
  {groups.map((group,index)=><details key={group} open={index<2}><summary>{group}</summary>{Object.entries(schema.globalTokens).filter(([,f])=>f.group===group).map(([key,f])=><Input key={key} definition={f} value={value[key]} onChange={v=>onChange({...value,[key]:v})}/>)}</details>)}
 </div>
}
function inheritedValue(key,tag,design) {
 const map={background:'background',text:'text',borderColor:'borderColor',borderWidth:'borderWidth',borderStyle:'borderStyle',alignment:'alignment',gap:'layoutGap',paddingBlock:'sectionSpacing',radius:'buttonRadius'}
 if(key==='fontSize')return design[(tag==='a'?'button':tag.startsWith('h')?tag:'body')+'Size']
 if(key==='fontWeight')return design[(tag==='a'?'button':tag.startsWith('h')?tag:'body')+'Weight']
 return design[map[key]] ?? schema.localTokens[key].default
}
export function LocalStyleField({value={},onChange,section,instance=section.type,design=defaultDesign,fixedTarget}) {
 const {selection,select}=useContext(SelectionContext)
 const target=fixedTarget??(selection?.section===instance?selection.target:'section')
 const targets=elementTargets(section),destination=targets.find(t=>t.id===target)||targets[0]
 const values=value[destination.id]||{}
 const update=(key,v)=>{
  const next={...values}
  if(v===undefined)delete next[key];else next[key]=v
  const all={...value,[destination.id]:next}
  if(!Object.keys(next).length)delete all[destination.id]
  onChange(all)
 }
 return <div className="studio-local-fields">
  {fixedTarget?<p>{destination.label}</p>:<label className="studio-control"><span>Element</span><select aria-label="Style target" value={destination.id} onChange={e=>select(instance,e.target.value)}>{targets.map(t=><option key={t.id} value={t.id}>{t.label}</option>)}</select></label>}
  <p className="studio-help">{Object.keys(values).length?`${Object.keys(values).length} local overrides. Other properties use global/site styles.`:'Use Global Style · no local overrides.'}</p>
  <button disabled={!Object.keys(values).length} onClick={()=>{const next={...value};delete next[destination.id];onChange(next)}}>Reset to Global</button>
  {Object.entries(localFields(destination.tag)).map(([key,f])=>{
   const overridden=Object.hasOwn(values,key),effective=inheritedValue(key,destination.tag,design)
   return <div className="studio-override" key={key}>
    <label className="studio-mode"><span>{f.label}</span><select aria-label={`${f.label} mode`} value={overridden?'local':'global'} onChange={e=>update(key,e.target.value==='local'?effective:undefined)}><option value="global">Use Global Style</option><option value="local">Override Locally</option></select></label>
    {overridden && <Input definition={f} value={values[key]} onChange={v=>update(key,v)} label={`Local ${f.label}`}/>}
   </div>
  })}
 </div>
}
