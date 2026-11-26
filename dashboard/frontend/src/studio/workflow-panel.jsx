import {useState} from 'react'
import {defaultGraphic,validateGraphic,SOURCE_COLOURS} from './workflow-graphic'
// Design properties of the real WorkflowFigure (schema v5). Text stays in the component's own
// accessible fields; colours of its surface and text use the local style controls below.
export function WorkflowGraphicField({value,onChange}) {
 const g=value||defaultGraphic,[error,setError]=useState('')
 function update(values){try{const next=validateGraphic({...g,...values});onChange(next);setError('')}catch(e){setError(e.message)}}
 return <section className="studio-graphic-panel" aria-label="Workflow graphic controls">
  <h3>Workflow graphic</h3><p>Website component: <code>WorkflowFigure</code> · Hero / graphic. Eyebrow, title, steps and outcome remain accessible text.</p>
  <label className="studio-control"><span>Graphic width (%)</span><input aria-label="Graphic width (%)" type="number" min="25" max="100" value={g.width} onChange={e=>update({width:e.target.valueAsNumber})}/></label>
  {['horizontal','vertical'].map(key=><label className="studio-control" key={key}><span>{key==='horizontal'?'Horizontal position':'Vertical position'}</span><select aria-label={`${key==='horizontal'?'Horizontal':'Vertical'} graphic position`} value={g[key]} onChange={e=>update({[key]:e.target.value})}>{['start','center','end'].map(v=><option key={v}>{v}</option>)}</select></label>)}
  <label className="studio-control"><span>Title size (%)</span><input aria-label="Graphic title size (%)" type="number" min="70" max="140" step="5" value={g.titleScale} onChange={e=>update({titleScale:e.target.valueAsNumber})}/></label>
  <label className="studio-control"><span>Accent rule colour</span><input aria-label="Graphic accent colour" type="color" value={g.accent||SOURCE_COLOURS.accent} onChange={e=>update({accent:e.target.value})}/></label>
  <label className="studio-control"><span>Step line and marker colour</span><input aria-label="Graphic step line colour" type="color" value={g.line||SOURCE_COLOURS.line} onChange={e=>update({line:e.target.value})}/></label>
  <label className="studio-control"><span>On phones</span><select aria-label="Graphic position on phones" value={g.mobileOrder} onChange={e=>update({mobileOrder:e.target.value})}><option value="after">Below the headline</option><option value="before">Above the headline</option></select></label>
  <button onClick={()=>{setError('');onChange({...defaultGraphic})}}>Restore original workflow graphic</button>
  {!!error&&<p role="alert">{error}</p>}
 </section>
}
