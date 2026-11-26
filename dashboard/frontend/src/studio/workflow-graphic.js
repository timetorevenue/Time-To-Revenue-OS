// WorkflowFigure is the real website component, pinned by source-manifest.json.
// Its properties belong to the native nested WorkflowFigure item; Hero stays a section.
export const WORKFLOW_COMPONENT = 'WorkflowFigure'
// Version 2 (schema v5): design properties of the candidate's text WorkflowFigure. null colours and
// scale 100 / order "after" are the website's own values, so the default renders the source unchanged.
// The version 1 SVG properties (source/asset/alt/fit/signal) have no destination in this component;
// migrations.json retains them in the draft's unsupported list instead of rendering them.
export const defaultGraphic = {width:100,horizontal:'center',vertical:'center',accent:null,line:null,titleScale:100,mobileOrder:'after'}
export const SOURCE_COLOURS = {accent:'#b89b63',line:'#b7aa91'}
const colour = v => v===null || (typeof v==='string' && /^#[0-9a-fA-F]{6}$/.test(v))
export function validateGraphic(value) {
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join()!==Object.keys(defaultGraphic).sort().join())throw Error('Invalid workflow graphic properties.')
 if(!['start','center','end'].includes(value.horizontal)||!['start','center','end'].includes(value.vertical)||!['after','before'].includes(value.mobileOrder))throw Error('Invalid workflow graphic option.')
 if(typeof value.width!=='number'||!Number.isFinite(value.width)||value.width<25||value.width>100)throw Error('Graphic width must be 25–100%.')
 if(typeof value.titleScale!=='number'||!Number.isFinite(value.titleScale)||value.titleScale<70||value.titleScale>140)throw Error('Graphic title size must be 70–140%.')
 if(!colour(value.accent)||!colour(value.line))throw Error('Invalid workflow graphic colour.')
 return structuredClone(value)
}
// Inline destinations on the figure itself; CSS custom properties feed the source's pseudo-elements
// (componentCss in design.js), whose defaults are the website's own values.
// Layout is emitted only when changed, so the source's own (responsive) sizing applies by default.
export function graphicStyle(g) {
 if(!g)return {}
 const placed=g.width!==100||g.horizontal!=='center'||g.vertical!=='center'
 return {...(placed?{width:g.width+'%',justifySelf:g.horizontal,alignSelf:g.vertical}:{}),
  ...(g.accent?{borderTopColor:g.accent}:{}),...(g.line?{'--studio-workflow-line':g.line}:{}),
  ...(g.titleScale!==100?{'--studio-workflow-title-scale':g.titleScale/100}:{})}
}
export const graphicAttributes=g=>g&&g.mobileOrder!=='after'?{'data-studio-mobile-order':g.mobileOrder}:{}
