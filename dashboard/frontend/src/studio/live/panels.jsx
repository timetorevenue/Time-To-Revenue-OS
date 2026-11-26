// Live website editor — professional panels: design controls (typography, colour, spacing, layout,
// effects, responsive, links, media), Code mode (owner CSS and source-file edits with highlighting and
// the exact generated source diff), Site (header scroll behaviour) and the Website Manager conversation.
// Every change goes through the shared operations (code.js applyOps) as one undoable history step, so a
// manual edit and the same Website Manager request produce identical documents and source.
// Revisit: when ops, the code layer or the manager API change. Last touched: 2026-11-22.
import {useEffect, useMemo, useRef, useState} from 'react'
import {BREAKPOINTS} from './model.js'
import {BRAND_COLORS, FONT_OPTIONS, HEADER_MODES, HEADER_MODE_LABELS, HEADER_DEFAULT, HEADER_BPS, cssProblem, fileProblem, linkProblem} from './code.js'
import {TEMPLATES} from './pages.js'

const bpLabel = id => BREAKPOINTS.find(b => b.id === id)?.label || id

// ---------------------------------------------------------------- small inputs
function Text({label, value, onCommit, placeholder, list, testid, width}) {
 const [v, setV] = useState(value ?? '')
 useEffect(() => setV(value ?? ''), [value])
 return <label className="live-field" style={width ? {width} : undefined}><span>{label}</span>
  <input value={v} placeholder={placeholder} list={list} aria-label={label} data-testid={testid} onChange={e => setV(e.target.value)}
   onBlur={() => { if ((value ?? '') !== v) onCommit(v) }} onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }}/></label>
}
function Choice({label, value, options, onChange, testid}) {
 return <label className="live-field"><span>{label}</span><select aria-label={label} data-testid={testid} value={value ?? ''} onChange={e => onChange(e.target.value)}>
  {options.map(o => typeof o === 'string' ? <option key={o} value={o}>{o || '—'}</option> : <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>
}
function Swatches({label, value, onPick}) {
 return <div className="live-swatches" role="group" aria-label={label}><span>{label}</span>
  {Object.entries(BRAND_COLORS).map(([name, hex]) => <button key={name} title={`${name} ${hex}`} aria-label={`${label} ${name}`} aria-pressed={String(value || '').toLowerCase() === hex.toLowerCase()} style={{background: hex}} onClick={() => onPick(hex)}/>)}
  <input type="color" aria-label={`${label} picker`} value={/^#[0-9a-f]{6}$/i.test(value || '') ? value : '#000000'} onChange={e => onPick(e.target.value)}/>
  {value && <button className="live-clear" onClick={() => onPick(null)} aria-label={`Clear ${label}`}>×</button>}
 </div>
}
const SIZES = ['12px', '14px', '16px', '18px', '20px', '24px', '28px', '32px', '40px', '48px', '56px', '64px', '72px', 'clamp(32px, 5vw, 64px)']
const SHADOWS = [{label: 'None', value: 'none'}, {label: 'Subtle', value: '0 1px 3px rgba(17,19,21,0.06), 0 4px 16px rgba(17,19,21,0.04)'},
 {label: 'Raised', value: '0 4px 24px rgba(17,19,21,0.10)'}, {label: 'Strong', value: '0 12px 40px rgba(17,19,21,0.18)'}]

// ---------------------------------------------------------------- design controls
export function DesignControls({item, at, setAt, bp, runOps, model, siteFiles, linkItem}) {
 const p = item.props
 const id = p.id
 const st = p.st?.[at] || {}
 const S = (values, verb) => runOps([{op: 'set_style', id, bp: at, values}], `${verb} at ${bpLabel(at)}`)
 const routes = useMemo(() => model.pages.filter(x => x.route !== '/_not-found').map(x => x.route), [model])
 const isMedia = item.type === 'El' && ['img', 'Image', 'video'].includes(p.tag)
 const [gradient, setGradient] = useState({from: '#F7F3EA', to: '#ECE9E2', angle: '180'})
 const overrides = BREAKPOINTS.map(b => ({...b, n: Object.keys(p.st?.[b.id] || {}).length}))
 return <div className="live-design" data-testid="live-design">
  <div className="live-bp-bar" role="group" aria-label="Breakpoint being styled">
   {overrides.map(b => <button key={b.id} aria-pressed={at === b.id} data-testid={`live-style-bp-${b.id}`} onClick={() => setAt(b.id === bp ? null : b.id)}>{b.label}{b.n ? ` · ${b.n}` : ''}</button>)}
  </div>
  <p className="live-help">Styling <strong>{bpLabel(at)}</strong>{at !== 'd' ? ' — overrides desktop at this width and narrower' : ' — applies everywhere unless a narrower breakpoint overrides it'}.</p>

  {linkItem && <section className="live-group" aria-label="Link">
   <h4>Link</h4>
   {linkItem.props.x?.href ? <p className="live-help">This link’s destination is computed by the site’s code ({model.codes[linkItem.props.x.href]?.preview}). Change it in Code mode.</p> : <>
    <Text label="Destination" testid="live-link-href" value={linkItem.props.a?.href} list="live-routes" placeholder="/pricing, https://…, #anchor, mailto:, tel:"
     onCommit={v => runOps([{op: 'set_link', id: linkItem.props.id, href: v}], `Link → ${v}`)}/>
    <datalist id="live-routes">{routes.map(r => <option key={r} value={r}/>)}</datalist>
    <label className="live-check"><input type="checkbox" data-testid="live-link-newtab" checked={linkItem.props.a?.target === '_blank'} onChange={e => runOps([{op: 'set_link', id: linkItem.props.id, href: linkItem.props.a?.href || '', newTab: e.target.checked}], e.target.checked ? 'Opens in a new tab' : 'Opens in the same tab')}/> Open in a new tab (adds rel="noopener noreferrer")</label>
    {linkItem.props.a?.href && linkProblem(linkItem.props.a.href, {routes: new Set(routes), files: siteFiles}) && <p role="alert" className="live-warn">{linkProblem(linkItem.props.a.href, {routes: new Set(routes), files: siteFiles})}</p>}
   </>}
  </section>}

  {item.type !== 'Txt' && <>
   <section className="live-group" aria-label="Typography"><h4>Typography</h4>
    <Choice label="Font" testid="live-font-family" value={FONT_OPTIONS.find(f => f.value === st.fontFamily)?.value || (st.fontFamily ? st.fontFamily : '')}
     options={[{label: 'Inherit (site default)', value: ''}, ...FONT_OPTIONS, ...(st.fontFamily && !FONT_OPTIONS.some(f => f.value === st.fontFamily) ? [{label: st.fontFamily, value: st.fontFamily}] : [])]}
     onChange={v => S({fontFamily: v || null}, v ? 'Font changed' : 'Font reset')}/>
    <div className="live-pair">
     <Text label="Size" testid="live-font-size" value={st.fontSize} list="live-sizes" placeholder="e.g. 20px" onCommit={v => S({fontSize: v || null}, `Font size ${v || 'reset'}`)}/>
     <Choice label="Weight" testid="live-font-weight" value={st.fontWeight} options={['', '300', '400', '500', '600', '700', '800']} onChange={v => S({fontWeight: v || null}, `Weight ${v || 'reset'}`)}/>
    </div>
    <datalist id="live-sizes">{SIZES.map(s => <option key={s} value={s}/>)}</datalist>
    <div className="live-pair">
     <Text label="Line height" value={st.lineHeight} placeholder="1.4" onCommit={v => S({lineHeight: v || null}, 'Line height')}/>
     <Text label="Letter spacing" value={st.letterSpacing} placeholder="-0.02em" onCommit={v => S({letterSpacing: v || null}, 'Letter spacing')}/>
    </div>
    <div className="live-pair">
     <Choice label="Case" value={st.textTransform} options={['', 'none', 'uppercase', 'lowercase', 'capitalize']} onChange={v => S({textTransform: v || null}, 'Case')}/>
     <Choice label="Style" value={st.fontStyle} options={['', 'normal', 'italic']} onChange={v => S({fontStyle: v || null}, 'Font style')}/>
    </div>
    <div className="live-row" role="group" aria-label="Text alignment"><span>Align</span>{['left', 'center', 'right', 'justify'].map(a => <button key={a} aria-pressed={st.textAlign === a} onClick={() => S({textAlign: st.textAlign === a ? null : a}, `Aligned ${a}`)}>{a}</button>)}</div>
    <Swatches label="Text colour" value={st.color} onPick={v => S({color: v}, 'Text colour')}/>
   </section>

   <section className="live-group" aria-label="Background"><h4>Colour & background</h4>
    <Swatches label="Background" value={st.backgroundColor} onPick={v => S({backgroundColor: v}, 'Background colour')}/>
    <div className="live-row"><span>Gradient</span>
     <input type="color" aria-label="Gradient from" value={gradient.from} onChange={e => setGradient({...gradient, from: e.target.value})}/>
     <input type="color" aria-label="Gradient to" value={gradient.to} onChange={e => setGradient({...gradient, to: e.target.value})}/>
     <input aria-label="Gradient angle" value={gradient.angle} size={3} onChange={e => setGradient({...gradient, angle: e.target.value.replace(/[^0-9]/g, '').slice(0, 3)})}/>°
     <button onClick={() => S({backgroundImage: `linear-gradient(${gradient.angle || 180}deg, ${gradient.from}, ${gradient.to})`}, 'Gradient')}>Apply</button>
     {st.backgroundImage && <button onClick={() => S({backgroundImage: null}, 'Background image removed')}>Remove</button>}
    </div>
   </section>

   <section className="live-group" aria-label="Spacing and size"><h4>Spacing & size</h4>
    <div className="live-quad">{['Top', 'Right', 'Bottom', 'Left'].map(side => <Text key={side} label={`Padding ${side.toLowerCase()}`} value={st['padding' + side]} placeholder="0px" onCommit={v => S({['padding' + side]: v || null}, `Padding ${side.toLowerCase()}`)}/>)}</div>
    <div className="live-quad">{['Top', 'Right', 'Bottom', 'Left'].map(side => <Text key={side} label={`Margin ${side.toLowerCase()}`} value={st['margin' + side]} placeholder="0px" onCommit={v => S({['margin' + side]: v || null}, `Margin ${side.toLowerCase()}`)}/>)}</div>
    <div className="live-pair">
     <Text label="Width" value={st.width} placeholder="auto / 100% / 320px" onCommit={v => S({width: v || null}, 'Width')}/>
     <Text label="Max width" value={st.maxWidth} placeholder="none / 72ch" onCommit={v => S({maxWidth: v || null}, 'Max width')}/>
    </div>
    <div className="live-pair">
     <Text label="Height" value={st.height} placeholder="auto" onCommit={v => S({height: v || null}, 'Height')}/>
     <Text label="Gap" value={st.gap} placeholder="16px" onCommit={v => S({gap: v || null}, 'Gap')}/>
    </div>
   </section>

   <section className="live-group" aria-label="Layout"><h4>Layout</h4>
    <div className="live-pair">
     <Choice label="Display" testid="live-display" value={st.display} options={['', 'block', 'flex', 'grid', 'inline-flex', 'inline-block', 'inline', 'none']} onChange={v => S({display: v || null}, `Display ${v || 'reset'}`)}/>
     <Choice label="Direction" value={st.flexDirection} options={['', 'row', 'column', 'row-reverse', 'column-reverse']} onChange={v => S({flexDirection: v || null}, 'Direction')}/>
    </div>
    <div className="live-pair">
     <Choice label="Justify" value={st.justifyContent} options={['', 'flex-start', 'center', 'flex-end', 'space-between', 'space-around', 'space-evenly']} onChange={v => S({justifyContent: v || null}, 'Justify')}/>
     <Choice label="Align items" value={st.alignItems} options={['', 'stretch', 'flex-start', 'center', 'flex-end', 'baseline']} onChange={v => S({alignItems: v || null}, 'Align items')}/>
    </div>
    <div className="live-row" role="group" aria-label="Grid columns"><span>Columns</span>{[1, 2, 3, 4, 6].map(n => <button key={n} data-testid={`live-cols-${n}`} aria-label={`${n} columns at ${bpLabel(at)}`}
     onClick={() => S({display: 'grid', gridTemplateColumns: n === 1 ? '1fr' : `repeat(${n}, minmax(0, 1fr))`}, `${n} column${n === 1 ? '' : 's'}`)}>{n}</button>)}
     <button data-testid="live-stack" onClick={() => S({display: 'flex', flexDirection: 'column'}, 'Stacked')}>Stack</button></div>
    <Text label="Grid template columns" value={st.gridTemplateColumns} placeholder="repeat(3, minmax(0, 1fr))" onCommit={v => S({gridTemplateColumns: v || null}, 'Grid columns')}/>
   </section>

   <section className="live-group" aria-label="Effects"><h4>Effects</h4>
    <div className="live-pair">
     <Text label="Corner radius" value={st.borderRadius} placeholder="12px" onCommit={v => S({borderRadius: v || null}, 'Corner radius')}/>
     <Choice label="Shadow" value={SHADOWS.find(s => s.value === st.boxShadow)?.value || ''} options={[{label: '—', value: ''}, ...SHADOWS]} onChange={v => S({boxShadow: v || null}, 'Shadow')}/>
    </div>
    <div className="live-pair">
     <Text label="Border" value={st.border} placeholder="1px solid #D8D0C2" onCommit={v => S({border: v || null}, 'Border')}/>
     <label className="live-field"><span>Opacity {st.opacity ?? 1}</span><input type="range" min="0" max="1" step="0.05" aria-label="Opacity" value={st.opacity ?? 1} onChange={e => S({opacity: e.target.value === '1' ? null : e.target.value}, 'Opacity')}/></label>
    </div>
   </section>

   {isMedia && <section className="live-group" aria-label="Media"><h4>Media</h4>
    <Text label="Alternative text" value={p.a?.alt} onCommit={v => runOps([{op: 'set_attr', id, name: 'alt', value: v}], 'Alt text')}/>
    <div className="live-pair">
     <Choice label="Fit" value={st.objectFit} options={['', 'cover', 'contain', 'fill', 'none', 'scale-down']} onChange={v => S({objectFit: v || null}, 'Fit')}/>
     <Text label="Focal point" value={st.objectPosition} placeholder="50% 30%" onCommit={v => S({objectPosition: v || null}, 'Focal point')}/>
    </div>
    <Text label="Aspect ratio" value={st.aspectRatio} placeholder="16 / 9" onCommit={v => S({aspectRatio: v || null}, 'Aspect ratio')}/>
    {p.tag === 'video' && <div className="live-row">{['controls', 'autoPlay', 'muted', 'loop', 'playsInline'].map(a => <label key={a} className="live-check"><input type="checkbox" checked={!!p.a?.[a]}
     onChange={e => runOps([{op: 'set_attr', id, name: a, value: e.target.checked || null}], `${a} ${e.target.checked ? 'on' : 'off'}`)}/> {a}</label>)}</div>}
   </section>}

   <section className="live-group" aria-label="Responsive"><h4>Responsive</h4>
    <div className="live-row"><button data-testid="live-hide-bp" aria-pressed={st.display === 'none'} onClick={() => S({display: st.display === 'none' ? null : 'none'}, st.display === 'none' ? 'Shown' : 'Hidden')}>{st.display === 'none' ? 'Show' : 'Hide'} on {bpLabel(at).toLowerCase()}</button>
     {p.st?.[at] && <button onClick={() => runOps([{op: 'set_style', id, bp: at, values: Object.fromEntries(Object.keys(p.st[at]).map(k => [k, null]))}], `${bpLabel(at)} styles cleared`)}>Reset {bpLabel(at).toLowerCase()}</button>}</div>
    <ul className="live-overrides">{overrides.filter(b => b.n).map(b => <li key={b.id}><strong>{b.label}</strong>: {Object.entries(p.st[b.id]).map(([k, v]) => `${k} ${v}`).join(' · ')}</li>)}</ul>
   </section>
  </>}
 </div>
}

// ---------------------------------------------------------------- code editor with highlighting
const RULES = {
 css: [/(\/\*[\s\S]*?\*\/)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(#[0-9a-fA-F]{3,8}\b|-?\b\d*\.?\d+(?:px|rem|em|%|vh|vw|s|ms|deg|fr)?\b)|(@[\w-]+)|([\w-]+)(?=\s*:[^:])/g, ['c', 's', 'n', 'k', 'p']],
 ts: [/(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)|\b(import|export|from|const|let|var|function|return|if|else|default|type|interface|as|async|await|new|class|extends|true|false|null|undefined)\b|(<\/?[A-Za-z][\w.]*)|(\b\d+(?:\.\d+)?\b)/g, ['c', 's', 'k', 't', 'n']],
}
const esc = t => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
export function highlight(text, lang) {
 const [re, names] = RULES[lang] || RULES.ts
 let out = '', last = 0
 for (const m of String(text).matchAll(re)) {
  out += esc(text.slice(last, m.index))
  const g = m.slice(1).findIndex(x => x !== undefined)
  out += `<span class="hl-${names[g]}">${esc(m[0])}</span>`
  last = m.index + m[0].length
 }
 return out + esc(String(text).slice(last))
}
export function SourceEditor({value, onChange, lang = 'ts', label, rows = 18, testid}) {
 const pre = useRef(null)
 const html = useMemo(() => highlight(value || '', lang), [value, lang])
 const lines = (value || '').split('\n').length
 return <div className="live-editor" style={{height: `${rows * 1.45}em`}}>
  <div className="live-editor-gutter" aria-hidden="true">{Array.from({length: Math.min(lines, 5000)}, (_, i) => <div key={i}>{i + 1}</div>)}</div>
  <div className="live-editor-body">
   <pre aria-hidden="true" ref={pre} dangerouslySetInnerHTML={{__html: html + '\n'}}/>
   <textarea spellCheck={false} aria-label={label} data-testid={testid} value={value || ''} onChange={e => onChange(e.target.value)}
    onScroll={e => { if (pre.current) { pre.current.scrollTop = e.target.scrollTop; pre.current.scrollLeft = e.target.scrollLeft }; const g = e.target.closest('.live-editor')?.querySelector('.live-editor-gutter'); if (g) g.scrollTop = e.target.scrollTop }}
    onKeyDown={e => { if (e.key === 'Tab' && !e.shiftKey) { e.preventDefault(); const t = e.currentTarget, s = t.selectionStart; onChange(t.value.slice(0, s) + '  ' + t.value.slice(t.selectionEnd)); requestAnimationFrame(() => { t.selectionStart = t.selectionEnd = s + 2 }) } }}/>
  </div>
 </div>
}

// ---------------------------------------------------------------- Code mode
export function CodePanel({client, code, runOps, model, setMessage, workingDocs}) {
 const [css, setCss] = useState(code.css || '')
 useEffect(() => setCss(code.css || ''), [code.css])
 const [files, setFiles] = useState(null)
 const [open, setOpen] = useState(null) // {path, pristine, text, exists}
 const [filter, setFilter] = useState('')
 const [diff, setDiff] = useState(null)
 const [newPath, setNewPath] = useState('')
 useEffect(() => { client.sourceFiles().then(r => setFiles(r.files), e => setMessage(e.message)) }, [client, setMessage])
 const cssProb = css.trim() ? cssProblem(css) : null
 const openFile = async path => {
  try { const f = await client.sourceFile(path); setOpen({path, pristine: f.text, exists: f.exists, text: code.files?.[path] ?? f.text ?? ''}) } catch (e) { setMessage(e.message) }
 }
 const edited = Object.keys(code.files || {})
 const visual = open && model.docs[open.path]
 const fileProb = open ? fileProblem(open.path, open.text) : null
 const showDiff = async () => {
  setDiff({loading: true})
  try { setDiff(await client.sourceDiff({docs: workingDocs(), code})) } catch (e) { setDiff({error: e.message}) }
 }
 return <div className="live-code-mode" data-testid="live-code-mode">
  <section className="live-group"><h4>Owner CSS</h4>
   <p className="live-help">Appended to <code>app/globals.css</code> in the real build and shown on the canvas. Plain rules and media queries; no <code>@import</code>, scripts or remote <code>url()</code> (use <code>url("asset:…")</code> or a site path).</p>
   <SourceEditor lang="css" label="Owner CSS" testid="live-css-editor" value={css} onChange={setCss} rows={12}/>
   {cssProb && <p role="alert" className="live-warn">{cssProb}</p>}
   <div className="live-row"><button className="live-primary" data-testid="live-css-apply" disabled={!!cssProb || css === (code.css || '')} onClick={() => runOps([{op: 'set_css', css}], 'Owner CSS applied')}>Apply CSS</button>
    <button disabled={css === (code.css || '')} onClick={() => setCss(code.css || '')}>Revert</button></div>
  </section>

  <section className="live-group"><h4>Source files</h4>
   <p className="live-help">Edit real website modules that the visual editor cannot represent (functional React components, data, config, styles). Edits are saved in the version, applied over the pinned source in the build and checked by <code>next build</code>. A file with element edits cannot also be edited as source; while a visual file is source-edited it is code-only.</p>
   {!!edited.length && <p data-testid="live-edited-files">Edited: {edited.map(f => <button key={f} className="live-link" onClick={() => openFile(f)}>{f}</button>)}</p>}
   <input aria-label="Filter source files" placeholder="Filter files…" value={filter} onChange={e => setFilter(e.target.value)}/>
   <ul className="live-files">{(files || []).filter(f => f.path.includes(filter)).slice(0, 80).map(f => <li key={f.path}><button className="live-link" data-file={f.path} onClick={() => openFile(f.path)}>{f.path}</button>
    <small>{f.visual ? `visual ${f.kind}` : 'code-only'}{code.files?.[f.path] !== undefined ? ' · edited' : ''}</small></li>)}</ul>
   <div className="live-row"><input aria-label="New file path" placeholder="components/NewThing.tsx" value={newPath} onChange={e => setNewPath(e.target.value)}/>
    <button disabled={!newPath || !!fileProblem(newPath, '')} onClick={() => setOpen({path: newPath, pristine: null, exists: false, text: code.files?.[newPath] ?? ''})}>New file</button></div>
   {open && <div className="live-file" data-testid="live-file-editor" data-path={open.path}>
    <div className="live-row"><strong>{open.path}</strong>{!open.exists && <small>(new file)</small>}<button onClick={() => setOpen(null)}>Close</button></div>
    {visual && <p className="live-warn">This is a visual document ({visual.kind}). While it carries a source edit, its element edits are refused on save; remove the source edit to edit it visually again.</p>}
    <SourceEditor lang={open.path.endsWith('.css') ? 'css' : 'ts'} label={`Source of ${open.path}`} testid="live-file-source" value={open.text} onChange={t => setOpen({...open, text: t})} rows={22}/>
    {fileProb && <p role="alert" className="live-warn">{fileProb}</p>}
    <div className="live-row">
     <button className="live-primary" data-testid="live-file-apply" disabled={!!fileProb || open.text === (code.files?.[open.path] ?? open.pristine)} onClick={() => runOps([{op: 'set_file', path: open.path, text: open.text === open.pristine ? null : open.text}], `Source edit ${open.path}`)}>Apply source edit</button>
     {code.files?.[open.path] !== undefined && <button onClick={() => { runOps([{op: 'set_file', path: open.path, text: null}], `Source edit removed: ${open.path}`); setOpen({...open, text: open.pristine ?? ''}) }}>Reset to website source</button>}
    </div>
   </div>}
  </section>

  <section className="live-group"><h4>Generated source</h4>
   <p className="live-help">The exact website source your working state generates (element edits + owner CSS + header behaviour + source edits), against the pinned live source. Build it in <em>Preview & build</em> to run <code>next build</code>.</p>
   <button data-testid="live-source-diff" onClick={showDiff}>Show source diff</button>
   {diff?.loading && <p role="status">Generating…</p>}
   {diff?.error && <p role="alert">{diff.error}</p>}
   {diff?.files && <div data-testid="live-source-diff-result"><p>{diff.files.length} file(s): {diff.changed.join(', ') || 'none'}</p>{diff.files.map(f => <details key={f.path}><summary>{f.path} · {f.status}</summary><pre className="live-diff">{f.diff}</pre></details>)}</div>}
  </section>
 </div>
}

// ---------------------------------------------------------------- Site: header scroll behaviour
export function SitePanel({code, runOps, preview, setPreview}) {
 const on = !!code.header
 const [h, setH] = useState(code.header || HEADER_DEFAULT)
 useEffect(() => setH(code.header || HEADER_DEFAULT), [code.header])
 const changed = JSON.stringify(h) !== JSON.stringify(code.header || null)
 return <div className="live-site" data-testid="live-site-panel">
  <section className="live-group"><h4>Header scrolling</h4>
   <label className="live-check"><input type="checkbox" data-testid="live-header-enabled" checked={on} onChange={e => runOps([{op: 'set_header', header: e.target.checked ? h : null}], e.target.checked ? 'Header scroll behaviour on' : 'Header scroll behaviour off (site default)')}/> Studio header scroll behaviour</label>
   <p className="live-help">{on ? 'Built into the site as a small client component mounted in app/layout.tsx with CSS in app/globals.css.' : 'Off: the header behaves as in the website source (sticky, transparent at the top, solid after scrolling, on every device).'}</p>
   {HEADER_BPS.map(b => <Choice key={b.id} label={`${b.label}`} testid={`live-header-${b.id}`} value={h[b.id]} options={HEADER_MODES.map(m => ({value: m, label: HEADER_MODE_LABELS[m]}))} onChange={v => setH({...h, [b.id]: v})}/>)}
   <label className="live-field"><span>Scroll distance before the header turns solid (px)</span><input type="number" min="0" max="400" aria-label="Header threshold" value={h.threshold} onChange={e => setH({...h, threshold: Math.max(0, Math.min(400, parseInt(e.target.value || '0', 10)))})}/></label>
   <div className="live-row"><button className="live-primary" data-testid="live-header-apply" disabled={on ? !changed : false} onClick={() => runOps([{op: 'set_header', header: h}], 'Header scroll behaviour updated')}>{on ? 'Apply header settings' : 'Turn on with these settings'}</button></div>
  </section>
  <section className="live-group"><h4>Preview header state on the canvas</h4>
   <p className="live-help">The canvas follows scrolling automatically; you can also choose a state here. Real scrolling (down hides, up reveals, menu open stays visible) is verified in the built site.</p>
   <div className="live-row" role="group" aria-label="Header state preview">{[['', 'As loaded'], ['top', 'At top'], ['scrolled', 'Scrolled'], ['hidden', 'Hidden (scrolling down)']].map(([k, l]) =>
    <button key={k || 'none'} aria-pressed={preview === k} data-testid={`live-header-state-${k || 'none'}`} onClick={() => setPreview(k)} disabled={!on && k !== ''}>{l}</button>)}</div>
  </section>
 </div>
}

// ---------------------------------------------------------------- Website Manager conversation
// Voice: the browser's speech recognition where it exists (Chrome, Edge, Safari), otherwise (or by
// choice) a recording transcribed by the TTROS server's local speech model. Either way the words land in
// the same box and go through the same Send, so spoken and typed requests take one path.
const SpeechRecognition = typeof window !== 'undefined' ? (window.SpeechRecognition || window.webkitSpeechRecognition || null) : null
const pref = (k, d) => { try { return localStorage.getItem(k) ?? d } catch { return d } }
const setPref = (k, v) => { try { localStorage.setItem(k, v) } catch { /* convenience only */ } }
const CAPS = {design: 'Design', content_seo: 'Content & SEO', media: 'Media', frontend: 'Frontend'}

// Page switches remount Puck; keep unfinished conversation state in the owning Studio session.
// Async replies also update this cache if the operator opens another page while a job runs.
function useManagerSession(sessions, page, field, initial) {
 const fallback = useRef(initial)
 const [value, setValue] = useState(() => sessions?.current[page]?.[field] ?? initial)
 useEffect(() => {
  if (!sessions) return
  const listeners = sessions.current.__listeners ||= new Set()
  const refresh = () => setValue(sessions.current[page]?.[field] ?? fallback.current)
  listeners.add(refresh)
  refresh()
  return () => listeners.delete(refresh)
 }, [sessions, page, field])
 const update = next => {
  const previous = sessions?.current[page]?.[field] ?? value
  const resolved = typeof next === 'function' ? next(previous) : next
  if (sessions) {
   sessions.current[page] = {...sessions.current[page], [field]:resolved}
   sessions.current.__listeners?.forEach(refresh => refresh())
  } else setValue(resolved)
 }
 return [value, update]
}
export function ManagerPanel({client, page, sel, bp, latest, pageDocs, code, computedOf, applyProposal, setMessage, renderPreview, onAccepted, sessions}) {
 const [status, setStatus] = useState(null)
 const [rows, setRows] = useManagerSession(sessions, page.id, 'rows', [])
 const [text, setText] = useManagerSession(sessions, page.id, 'text', '')
 const [busy, setBusy] = useManagerSession(sessions, page.id, 'busy', false)
 const [preview, setPreview] = useState(null)
 const listRef = useRef(null)
 // voice
 const [voiceServer, setVoiceServer] = useState(null)
 const [voiceMode, setVoiceMode] = useState(() => pref('ttros-studio:voice-mode', SpeechRecognition ? 'browser' : 'server'))
 const [voice, setVoice] = useState({state: 'idle', note: ''})
 const [fromVoice, setFromVoice] = useManagerSession(sessions, page.id, 'fromVoice', false)
 const [speak, setSpeak] = useState(() => pref('ttros-studio:speak', '0') === '1')
 const recRef = useRef(null), stopTimer = useRef(null), before = useRef('')
 useEffect(() => { client.voice().then(setVoiceServer, () => setVoiceServer({server_transcription: 'unavailable', detail: 'Voice status unavailable.'})) }, [client])
 useEffect(() => () => { try { recRef.current?.stop?.() } catch { /* already stopped */ } try { window.speechSynthesis?.cancel() } catch { /* none */ } }, [])
 const mode = voiceMode === 'browser' && !SpeechRecognition ? 'server' : voiceMode
 const say = reply => { if (!speak || !reply || !('speechSynthesis' in window)) return; try { window.speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(String(reply).slice(0, 600)); u.lang = 'en-CA'; window.speechSynthesis.speak(u) } catch { /* speech output is optional */ } }
 const stopVoice = () => { clearTimeout(stopTimer.current); try { recRef.current?.stop() } catch { /* already stopped */ } }
 const startBrowser = () => {
  const rec = new SpeechRecognition()
  rec.lang = navigator.language || 'en-CA'
  rec.interimResults = true
  rec.continuous = false
  rec.maxAlternatives = 1
  before.current = text.trim() ? text.trimEnd() + ' ' : ''
  rec.onresult = e => {
   let final = '', interim = ''
   for (let i = 0; i < e.results.length; i++) { const r = e.results[i]; if (r.isFinal) final += r[0].transcript; else interim += r[0].transcript }
   setText(before.current + final + interim)
   if (final.trim()) setFromVoice(true)
  }
  rec.onerror = e => {
   const blocked = ['not-allowed', 'service-not-allowed'].includes(e.error)
   setVoice({state: 'idle', note: blocked ? 'Microphone or speech recognition is blocked for this site. Allow it in the browser, or switch Voice to “Local transcription”.'
    : e.error === 'no-speech' ? 'No speech heard. Try again.' : `Browser speech recognition stopped (${e.error}). You can switch Voice to “Local transcription”.`})
  }
  rec.onend = () => setVoice(v => (v.state === 'listening' ? {state: 'idle', note: ''} : v))
  recRef.current = rec
  rec.start()
  setVoice({state: 'listening', note: 'Listening… speak your request; it appears in the box.'})
 }
 const startRecording = async () => {
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') { setVoice({state: 'idle', note: 'This browser cannot record audio here. Type, or use your device dictation (macOS: press Fn twice; Windows: Win+H).'}); return }
  if (voiceServer?.server_transcription !== 'available') { setVoice({state: 'idle', note: voiceServer?.detail || 'Local transcription is unavailable.'}); return }
  let stream
  try { stream = await navigator.mediaDevices.getUserMedia({audio: true}) } catch (e) { setVoice({state: 'idle', note: `Microphone not available: ${e.message}`}); return }
  const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus', 'audio/ogg'].find(t => MediaRecorder.isTypeSupported?.(t)) || ''
  const rec = new MediaRecorder(stream, type ? {mimeType: type} : undefined)
  const chunks = []
  rec.ondataavailable = e => { if (e.data?.size) chunks.push(e.data) }
  rec.onstop = async () => {
   stream.getTracks().forEach(t => t.stop())
   const blob = new Blob(chunks, {type: rec.mimeType || type || 'audio/webm'})
   if (!blob.size) { setVoice({state: 'idle', note: 'Nothing was recorded.'}); return }
   setVoice({state: 'transcribing', note: 'Transcribing on the TTROS server (local model, no external service)…'})
   try {
    const audio = await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(String(fr.result).split(',')[1]); fr.onerror = rej; fr.readAsDataURL(blob) })
    const r = await client.transcribe(audio, blob.type)
    if (!r.text.trim()) { setVoice({state: 'idle', note: 'No words were recognised. Try again closer to the microphone.'}); return }
    setText(t => (t.trim() ? t.trimEnd() + ' ' : '') + r.text.trim())
    setFromVoice(true)
    setVoice({state: 'idle', note: `Transcribed locally (${r.engine}, ${r.seconds}s). Check it, then Send.`})
   } catch (e) { setVoice({state: 'idle', note: `Transcription failed: ${e.message}`}) }
  }
  recRef.current = rec
  rec.start()
  stopTimer.current = setTimeout(() => { try { rec.stop() } catch { /* stopped */ } }, 60000)
  setVoice({state: 'recording', note: 'Recording… press the microphone again when you finish (one minute at most).'})
 }
 const mic = () => {
  if (voice.state === 'listening' || voice.state === 'recording') { stopVoice(); if (voice.state === 'listening') setVoice({state: 'idle', note: ''}); return }
  if (voice.state === 'transcribing' || busy) return
  if (mode === 'browser') { try { startBrowser() } catch (e) { setVoice({state: 'idle', note: `Speech recognition could not start (${e.message}). Switch Voice to “Local transcription”.`}) } }
  else startRecording()
 }
 useEffect(() => { client.manager().then(s => { setStatus(s); setRows(previous => previous.length ? previous : (s.turns || []).filter(t => t.request).map(t => ({...t, past: true}))) }, e => setStatus({error: e.message})) }, [client])
 // Start at the latest reply, keeping its explanation and decision controls together.
 useEffect(() => {
  const list = listRef.current
  if (!list) return
  const reveal = () => { const last=list.lastElementChild; if (last) list.scrollTop += last.getBoundingClientRect().top-list.getBoundingClientRect().top }
  reveal()
  const observer = new ResizeObserver(reveal)
  observer.observe(list)
  return () => observer.disconnect()
 }, [rows])
 const selId = sel?.item?.props?.id || null
 const send = async () => {
  const request = text.trim()
  if (!request || busy) return
  if (voice.state === 'listening' || voice.state === 'recording') stopVoice()
  setBusy(true)
  const sent = {docs: pageDocs(), code}
  setRows(r => [...r, {mine: true, request, input: fromVoice ? 'voice' : 'typed', at: new Date().toISOString(), context: {page: page.label, selected: selId, bp}}])
  setText('')
  try {
   const d = new Date()
   const r = await client.managerTurn({text: request, page: page.id, selected: selId, bp, base_version: latest, docs: sent.docs, code: sent.code, computed: selId ? computedOf(selId) : null,
    input: fromVoice ? 'voice' : 'typed', today: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`})
   setRows(rs => [...rs, {...r, sent}])
   say(r.reply + (r.reason ? ` Rejected: ${r.reason}` : ''))
  } catch (e) { setRows(rs => [...rs, {outcome: 'error', reply: e.message}]); say(e.message) } finally { setBusy(false); setFromVoice(false) }
 }
 const decide = async (row, verdict) => {
  const p = row.proposal
  try {
   if (verdict === 'accept') {
    await client.acceptProposal(p.id, {base_version: latest, docs: pageDocs(), code})
    applyProposal(p)
    onAccepted(p.id)
   } else await client.rejectProposal(p.id, {base_version: latest, docs: pageDocs(), code})
   setRows(rs => rs.map(x => x === row ? {...x, decided: verdict === 'accept' ? 'accepted' : 'rejected'} : x))
  } catch (e) { setMessage(`${verdict === 'accept' ? 'Not applied' : 'Not rejected'}: ${e.message}`); setRows(rs => rs.map(x => x === row ? {...x, decideError: e.message} : x)) }
 }
 const route = r => r.route === 'deterministic' ? 'Deterministic · 0 model calls' : r.route === 'model' ? `Model · ${status?.model?.model || 'website.manager'} ${status?.model?.effort || ''} · ${r.usage?.total_tokens ?? 0} tokens` : r.route || ''
 return <div className="live-manager" data-testid="live-manager">
  <p className="live-help live-manager-intro">Describe a change, or use the microphone. Preview each proposal before you apply it.</p>
  <details className="live-manager-about"><summary>How Website Manager works</summary><p className="live-help">Ask for changes to this page in plain words. Simple requests (links, fonts, sizes, layout per breakpoint, header modes, CSS) are read by deterministic rules; others go to the <code>website.manager</code> agent ({status ? (status.model_route === 'available' ? `${status.model?.model || 'pinned model'} ${status.model?.effort || ''}` : 'model route held: ' + (status.state || 'not released')) : '…'}). Each request is a Job with a receipt; you preview the exact source change and accept or reject it. Undo works as for any edit.</p></details>
  <p className="live-context" data-testid="live-manager-context"><span>{page.label}</span><span>{selId ? `${sel.item.props.tag || sel.item.type} ${selId}` : 'nothing selected'}</span><span>{bpLabel(bp)}</span><span>base v{latest}</span></p>
  <ol className="live-thread" ref={listRef} data-testid="live-manager-thread">
   {rows.map((r, i) => r.mine ? <li key={i} className="mine" data-input={r.input}><p>{r.request}</p><small>{r.input === 'voice' ? '🎙 spoken · ' : ''}{r.context.page} · {r.context.selected || 'no selection'} · {bpLabel(r.context.bp)}</small></li> :
    r.past ? <li key={i} className="past"><p><strong>You:</strong> {r.request}</p><small>{r.job_id} · {r.outcome}{r.proposal ? ` · ${r.proposal}` : ''} · {r.at}</small></li> :
    <li key={i} className={`agent ${r.outcome}`} data-outcome={r.outcome} data-job={r.job_id}>
     <p>{r.reply}</p>
     {r.reason && <p role="alert" className="live-warn">Rejected by validation: {r.reason}</p>}
     {r.missing?.length > 0 && <ul>{r.missing.map(m => <li key={m}>{m}</li>)}</ul>}
     {!r.proposal&&<small>{r.job_id} · {route(r)}{r.capability ? ` · ${CAPS[r.capability] || r.capability}` : ''}{r.input === 'voice' ? ' · spoken request' : ''}{r.failure_class ? ` · ${r.failure_class}` : ''}</small>}
     {r.proposal && <div className="live-proposal" data-testid="live-proposal" data-proposal={r.proposal.id} data-status={r.decided || 'open'}>
      <details className="live-proposal-details"><summary>Change details</summary><small>{r.job_id} · {route(r)}{r.capability ? ` · ${CAPS[r.capability] || r.capability}` : ''}{r.input === 'voice' ? ' · spoken request' : ''}</small><ul className="live-ops">{r.proposal.ops.map((o, j) => <li key={j}><code>{o.op}</code> {o.id || o.doc || o.parent || ''} {o.bp ? `@${o.bp}` : ''} {o.href || ''} {o.name ? `${o.name} = ${o.value}` : ''} {o.values ? Object.entries(o.values).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`).join('; ') : ''}{o.header ? `desktop ${o.header.desktop}, tablet ${o.header.tablet}, mobile ${o.header.mobile}` : ''}{o.settings ? `${o.settings.title} → ${o.op === 'create_post' ? '/blog/' : '/'}${o.settings.slug}` : ''}{o.node ? `<${o.node.props.tag || o.node.props.name}>` : ''}</li>)}</ul>
      <details><summary>Source change ({r.proposal.source_diff?.changed?.join(', ') || 'none'})</summary>{(r.proposal.source_diff?.files || []).map(f => <pre key={f.path} className="live-diff">{f.diff}</pre>)}</details></details>
      {r.decided ? <p className="live-help">{r.decided === 'accepted' ? 'Accepted and applied (undo available). Save a version to keep it.' : 'Rejected.'}</p> :
       <div className="live-row"><button data-testid="live-proposal-preview" onClick={() => setPreview({p: r.proposal, html: renderPreview(r.proposal, r.sent), bp: r.proposal.bp})}>Preview</button>
        <button className="live-primary" data-testid="live-proposal-accept" onClick={() => decide(r, 'accept')}>Accept</button>
        <button data-testid="live-proposal-reject" onClick={() => decide(r, 'reject')}>Reject</button></div>}
      {r.decideError && <p role="alert" className="live-warn">{r.decideError}</p>}
     </div>}
    </li>)}
   {busy && <li className="agent pending" role="status">Working… (Job running)</li>}
  </ol>
  <div className="live-compose">
   <textarea aria-label="Ask the Website Manager" data-testid="live-manager-input" rows={3} value={text} placeholder={selId ? 'e.g. “Use https://… on this button”, “add a soft shadow and a hover lift”, or press the microphone' : 'e.g. create a landing page called “Spring offer” · write a blog post titled “…” · or select an element'}
    onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send() }}/>
   <div className="live-compose-actions">
    <button className={`live-mic${voice.state === 'listening' || voice.state === 'recording' ? ' on' : ''}`} data-testid="live-manager-mic" data-voice-state={voice.state} data-voice-mode={mode}
     aria-pressed={voice.state === 'listening' || voice.state === 'recording'} aria-label={voice.state === 'listening' || voice.state === 'recording' ? 'Stop dictation' : 'Dictate a request'}
     title={mode === 'browser' ? 'Dictate with the browser’s speech recognition' : 'Record and transcribe on the TTROS server (local model)'} disabled={busy || voice.state === 'transcribing'} onClick={mic}>
     {voice.state === 'listening' || voice.state === 'recording' ? '■ Stop' : voice.state === 'transcribing' ? '… ' : '🎙 Dictate'}</button>
    <button className="live-primary" data-testid="live-manager-send" disabled={busy || !text.trim()} onClick={send}>{busy ? 'Working…' : 'Send'}</button>
   </div>
  </div>
  <div className="live-voice" data-testid="live-voice">
   <label>Dictation <select aria-label="Voice input" data-testid="live-voice-mode" value={mode} onChange={e => { setVoiceMode(e.target.value); setPref('ttros-studio:voice-mode', e.target.value) }}>
    <option value="browser" disabled={!SpeechRecognition}>Browser speech recognition{SpeechRecognition ? '' : ' (not in this browser)'}</option>
    <option value="server" disabled={voiceServer && voiceServer.server_transcription !== 'available'}>Local transcription (TTROS server){voiceServer && voiceServer.server_transcription !== 'available' ? ' (unavailable)' : ''}</option></select></label>
   <label className="live-check"><input type="checkbox" data-testid="live-speak" checked={speak} onChange={e => { setSpeak(e.target.checked); setPref('ttros-studio:speak', e.target.checked ? '1' : '0'); if (!e.target.checked) { try { window.speechSynthesis?.cancel() } catch { /* none */ } } }}/> Read replies aloud</label>
   {voice.note && <p role="status" className="live-help" data-testid="live-voice-note">{voice.note}</p>}
  </div>
  {preview && <div className="live-preview" data-testid="live-proposal-preview-frame"><div className="live-row"><strong>Proposal preview · {page.label} · {bpLabel(preview.bp)}</strong>
   {BREAKPOINTS.map(b => <button key={b.id} aria-pressed={preview.bp === b.id} onClick={() => setPreview({...preview, bp: b.id})}>{b.label}</button>)}<button onClick={() => setPreview(null)}>Close</button></div>
   <iframe title="Proposal preview" sandbox="allow-same-origin" srcDoc={preview.html} style={{width: BREAKPOINTS.find(b => b.id === preview.bp).width, height: 600, maxWidth: '100%'}}/></div>}
 </div>
}

// ---------------------------------------------------------------- created pages: new page / post
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }
export const slugify = t => String(t || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60).replace(/-+$/, '')
const listOf = v => String(v || '').split(',').map(x => x.trim()).filter(Boolean)
const imageChoices = assets => [{label: 'Default social image (/images/site-og.png)', value: ''},
 ...assets.library.filter(a => a.type.startsWith('image/') && a.type !== 'image/svg+xml').map(a => ({label: `${a.name} (Studio library)`, value: a.ref})),
 ...assets.site.filter(a => /^\/images\/.+\.(png|jpe?g|webp)$/i.test(a.path)).map(a => ({label: `${a.path} (website file)`, value: a.path}))]

export function NewPageDialog({kind, model, code, assets, onCancel, onCreate}) {
 const [title, setTitle] = useState('')
 const [slug, setSlug] = useState('')
 const [touched, setTouched] = useState(false)
 const [description, setDescription] = useState('')
 const [template, setTemplate] = useState(kind === 'post' ? 'article' : 'campaign')
 const [category, setCategory] = useState('Insights')
 const [tags, setTags] = useState('')
 const [cover, setCover] = useState('')
 const [error, setError] = useState('')
 const templates = kind === 'post' ? TEMPLATES.post : TEMPLATES.landing
 const finalSlug = touched ? slug : slugify(title)
 const submit = () => {
  const settings = {slug: finalSlug, title: title.trim(), description: description.trim(), template, status: 'draft'}
  if (kind === 'post') Object.assign(settings, {category: category.trim(), tags: listOf(tags), date: today(), ...(cover ? {coverImage: cover, coverImageAlt: title.trim()} : {})})
  const r = onCreate(kind, settings)
  if (!r) setError('Not created — see the status line for the reason.')
 }
 return <div className="live-modal" role="dialog" aria-modal="true" aria-label={kind === 'post' ? 'New blog post' : 'New landing page'} data-testid="live-new-dialog">
  <div className="live-modal-card">
   <h3>{kind === 'post' ? 'New blog post' : 'New landing page'}</h3>
   <p className="live-help">{kind === 'post' ? 'A new post in the site’s own blog: its record is added to content/blog.ts, its body is a new source file, and the blog index, related posts, search metadata and sitemap pick it up when it is published.' : 'A new page of the website with its own address, search metadata and sitemap entry (when published). Edit it like any page; it is saved with your versions.'} It starts as a draft.</p>
   <label className="live-field"><span>Title</span><input autoFocus data-testid="live-new-title" value={title} onChange={e => setTitle(e.target.value)}/></label>
   <label className="live-field"><span>Address</span><span className="live-slug">{kind === 'post' ? '/blog/' : '/'}<input data-testid="live-new-slug" value={finalSlug} onChange={e => { setTouched(true); setSlug(e.target.value) }}/></span></label>
   <label className="live-field"><span>Description (search and social)</span><textarea rows={2} data-testid="live-new-description" value={description} onChange={e => setDescription(e.target.value)}/></label>
   <label className="live-field"><span>Template</span><select data-testid="live-new-template" value={template} onChange={e => setTemplate(e.target.value)}>{Object.entries(templates).map(([id, t]) => <option key={id} value={id}>{t.label}: {t.summary}</option>)}</select></label>
   {kind === 'post' && <>
    <label className="live-field"><span>Category</span><input data-testid="live-new-category" value={category} onChange={e => setCategory(e.target.value)}/></label>
    <label className="live-field"><span>Tags (comma separated)</span><input data-testid="live-new-tags" value={tags} onChange={e => setTags(e.target.value)}/></label>
    <label className="live-field"><span>Cover image</span><select data-testid="live-new-cover" value={cover} onChange={e => setCover(e.target.value)}>{imageChoices(assets).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>
   </>}
   {error && <p role="alert" className="live-warn">{error}</p>}
   <div className="live-row"><button className="live-primary" data-testid="live-new-create" disabled={!title.trim() || !description.trim() || !finalSlug} onClick={submit}>Create {kind === 'post' ? 'post' : 'page'}</button><button onClick={onCancel}>Cancel</button></div>
  </div>
 </div>
}

// ---------------------------------------------------------------- page & SEO settings
export function PagePanel({page, model, code, runOps, assets, openPage, setMessage}) {
 const doc = page.created === 'post' ? page.post_doc : page.doc
 const s = page.created ? code.pages?.[doc] : null
 if (!s) {
  const rec = page.data?.['content/blog.ts']
  return <div className="live-page-settings" data-testid="live-page-settings" data-created="false">
   <p className="live-help"><strong>{page.label}</strong> · <code>{page.route}</code> is part of the imported website. Its title, description and social image are set in its source: {rec ? <>this blog post’s record fields (title, description, excerpt, category…) are in the Inspector when nothing is selected.</> : <>edit <code>{page.doc}</code>’s <code>metadata</code> in Code mode.</>}</p>
   <p className="live-help">Pages and posts created here get full settings in this panel.</p>
  </div>
 }
 const set = (values, note) => runOps([{op: 'set_page', doc, values}], note)
 const post = s.kind === 'post'
 const posts = [...(model.docs['content/blog.ts']?.tree || []).map(r => r.props.key), ...Object.values(code.pages || {}).filter(x => x.kind === 'post' && x.slug !== s.slug).map(x => x.slug)]
 return <div className="live-page-settings" data-testid="live-page-settings" data-created="true" data-doc={doc}>
  <p className="live-help">{post ? 'Blog post' : 'Landing page'} created in the Studio · <code>{page.route}</code> · source <code>{doc}</code>. Status <strong>{s.status}</strong>{s.status !== 'published' ? ' (hidden from search and the sitemap)' : ''}. Publishing the website itself is separate (Releases).</p>
  <Choice label="Status" testid="live-page-status" value={s.status} options={post ? ['draft', 'coming-soon', 'published'] : ['draft', 'published']} onChange={v => set({status: v}, `Status ${v}`)}/>
  <Text label="Title" testid="live-page-title" value={s.title} onCommit={v => set({title: v}, 'Title changed')}/>
  <Text label="SEO title (browser tab and search)" testid="live-page-seo-title" value={s.seoTitle} placeholder={s.title} onCommit={v => set({seoTitle: v}, 'SEO title changed')}/>
  <label className="live-field"><span>Description (search and social)</span><textarea rows={3} data-testid="live-page-description" defaultValue={s.description} key={s.description} onBlur={e => { if (e.target.value !== s.description) set({description: e.target.value}, 'Description changed') }}/></label>
  {post ? <>
   <label className="live-field"><span>Excerpt (blog index card)</span><textarea rows={2} defaultValue={s.excerpt || ''} key={s.excerpt} onBlur={e => { if (e.target.value !== (s.excerpt || '')) set({excerpt: e.target.value}, 'Excerpt changed') }}/></label>
   <Text label="Category" testid="live-page-category" value={s.category} onCommit={v => set({category: v}, 'Category changed')}/>
   <Text label="Tags (comma separated)" testid="live-page-tags" value={(s.tags || []).join(', ')} onCommit={v => set({tags: listOf(v)}, 'Tags changed')}/>
   <Text label="Target keyword" value={s.targetKeyword} onCommit={v => set({targetKeyword: v}, 'Target keyword changed')}/>
   <Text label="Secondary keywords" value={(s.secondaryKeywords || []).join(', ')} onCommit={v => set({secondaryKeywords: listOf(v)}, 'Keywords changed')}/>
   <Text label="Date" value={s.date} onCommit={v => set({date: v}, 'Date changed')}/>
   <Text label="Reading time (blank = from the text)" value={s.readingTime} onCommit={v => set({readingTime: v}, 'Reading time changed')}/>
   <Choice label="Cover / social image" testid="live-page-cover" value={s.coverImage || ''} options={imageChoices(assets)} onChange={v => set({coverImage: v || null, ...(v && !s.coverImageAlt ? {coverImageAlt: s.title} : {})}, 'Cover image changed')}/>
   <Text label="Cover image alt text" value={s.coverImageAlt} onCommit={v => set({coverImageAlt: v}, 'Cover alt text changed')}/>
   <Choice label="Pillar (guide this post belongs to)" value={s.pillar || ''} options={['', ...(model.docs['content/blog.ts']?.tree || []).map(r => r.props.key)]} onChange={v => set({pillar: v || null}, 'Pillar changed')}/>
   <label className="live-field"><span>Related posts</span><select multiple size={5} data-testid="live-page-related" value={s.relatedPosts || []} onChange={e => set({relatedPosts: [...e.target.selectedOptions].map(o => o.value).slice(0, 6)}, 'Related posts changed')}>{posts.map(x => <option key={x} value={x}>{x}</option>)}</select></label>
  </> : <>
   <Text label="Keywords (comma separated)" value={(s.keywords || []).join(', ')} onCommit={v => set({keywords: listOf(v)}, 'Keywords changed')}/>
   <Choice label="Social image (Open Graph / Twitter)" testid="live-page-og" value={s.ogImage || ''} options={imageChoices(assets)} onChange={v => set({ogImage: v || null}, 'Social image changed')}/>
   <Text label="Social image alt text" value={s.ogImageAlt} onCommit={v => set({ogImageAlt: v}, 'Social image alt text changed')}/>
   <label className="live-check"><input type="checkbox" data-testid="live-page-noindex" checked={!!s.noindex} onChange={e => set({noindex: e.target.checked || null}, e.target.checked ? 'Hidden from search' : 'Shown in search')}/> Hide from search engines (noindex, not in the sitemap)</label>
  </>}
  <details className="live-danger"><summary>Remove this {post ? 'post' : 'page'}</summary>
   <p className="live-help">Removes it from the working version (undo restores it). Saved versions keep it.</p>
   <button data-testid="live-page-remove" onClick={() => { if (runOps([{op: 'remove_page', doc}], `Removed ${page.route}`)) setTimeout(() => openPage('home'), 60) }}>Remove {page.route}</button>
  </details>
 </div>
}

// ---------------------------------------------------------------- image editor (browser canvas)
const RATIOS = [['Original', null], ['16:9', 16 / 9], ['4:3', 4 / 3], ['1:1', 1], ['3:4', 3 / 4], ['1.91:1 (social)', 1.91]]
export function ImageEditor({asset, client, onClose, onSaved}) {
 const [img, setImg] = useState(null)
 const [ratio, setRatio] = useState(null)
 const [focusX, setFocusX] = useState(50), [focusY, setFocusY] = useState(50)
 const [width, setWidth] = useState(1600)
 const [rotate, setRotate] = useState(0)
 const [flip, setFlip] = useState(false)
 const [format, setFormat] = useState('image/webp')
 const [quality, setQuality] = useState(0.86)
 const [busy, setBusy] = useState('')
 const preview = useRef(null)
 useEffect(() => { const i = new Image(); i.crossOrigin = 'use-credentials'; i.onload = () => { setImg(i); setWidth(Math.min(1600, i.naturalWidth)) }; i.src = client.assetUrl(asset.id) }, [asset, client])
 const render = () => {
  if (!img) return null
  const rotated = rotate % 180 !== 0
  const W = rotated ? img.naturalHeight : img.naturalWidth, H = rotated ? img.naturalWidth : img.naturalHeight
  const base = document.createElement('canvas'); base.width = W; base.height = H
  const b = base.getContext('2d')
  b.translate(W / 2, H / 2); b.rotate(rotate * Math.PI / 180); if (flip) b.scale(-1, 1)
  b.drawImage(img, -img.naturalWidth / 2, -img.naturalHeight / 2)
  let cw = W, ch = H
  if (ratio) { if (W / H > ratio) cw = Math.round(H * ratio); else ch = Math.round(W / ratio) }
  const cx = Math.round((W - cw) * focusX / 100), cy = Math.round((H - ch) * focusY / 100)
  const outW = Math.min(width, cw), outH = Math.round(ch * outW / cw)
  const out = document.createElement('canvas'); out.width = outW; out.height = outH
  out.getContext('2d').drawImage(base, cx, cy, cw, ch, 0, 0, outW, outH)
  return out
 }
 useEffect(() => { const c = render(); const el = preview.current; if (!c || !el) return; el.width = c.width; el.height = c.height; el.getContext('2d').drawImage(c, 0, 0) }) // eslint-disable-line react-hooks/exhaustive-deps
 const save = async () => {
  const c = render()
  if (!c) return
  setBusy('Saving…')
  try {
   const blob = await new Promise(r => c.toBlob(r, format, quality))
   const data = await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(String(fr.result).split(',')[1]); fr.onerror = rej; fr.readAsDataURL(blob) })
   const ext = {'image/webp': 'webp', 'image/jpeg': 'jpg', 'image/png': 'png'}[format]
   const name = `${asset.name.replace(/\.[a-z0-9]+$/i, '')}-edited-${c.width}x${c.height}.${ext}`
   const added = await client.derive({name, data, parent: asset.id, edits: {ratio: RATIOS.find(r => r[1] === ratio)[0], focus: [focusX, focusY], width: c.width, height: c.height, rotate, flip, format, quality}})
   onSaved(added)
  } catch (e) { setBusy(`Not saved: ${e.message}`) }
 }
 return <div className="live-modal" role="dialog" aria-modal="true" aria-label="Edit image" data-testid="live-image-editor">
  <div className="live-modal-card wide">
   <h3>Edit image · {asset.name}</h3>
   <p className="live-help">Crop, resize, rotate and convert. Saved as a new library asset (the original is kept); provenance records the edit.</p>
   <div className="live-image-edit">
    <canvas ref={preview} data-testid="live-image-preview" style={{maxWidth: '100%', maxHeight: 360, background: '#f2efe8'}}/>
    <div>
     <Choice label="Crop" value={RATIOS.findIndex(r => r[1] === ratio)} options={RATIOS.map((r, i) => ({label: r[0], value: i}))} onChange={v => setRatio(RATIOS[Number(v)][1])} testid="live-image-ratio"/>
     <label className="live-field"><span>Focus left–right {focusX}%</span><input type="range" min="0" max="100" value={focusX} onChange={e => setFocusX(Number(e.target.value))}/></label>
     <label className="live-field"><span>Focus top–bottom {focusY}%</span><input type="range" min="0" max="100" value={focusY} onChange={e => setFocusY(Number(e.target.value))}/></label>
     <label className="live-field"><span>Width (px, max)</span><input type="number" min="64" max="4000" data-testid="live-image-width" value={width} onChange={e => setWidth(Math.max(64, Math.min(4000, Number(e.target.value) || 64)))}/></label>
     <div className="live-row"><button onClick={() => setRotate((rotate + 270) % 360)}>⟲ Rotate</button><button onClick={() => setRotate((rotate + 90) % 360)}>⟳ Rotate</button><button aria-pressed={flip} onClick={() => setFlip(!flip)}>Flip</button></div>
     <Choice label="Format" value={format} options={[{label: 'WebP (smallest)', value: 'image/webp'}, {label: 'JPEG', value: 'image/jpeg'}, {label: 'PNG (lossless)', value: 'image/png'}]} onChange={setFormat}/>
     {format !== 'image/png' && <label className="live-field"><span>Quality {Math.round(quality * 100)}</span><input type="range" min="0.5" max="1" step="0.02" value={quality} onChange={e => setQuality(Number(e.target.value))}/></label>}
    </div>
   </div>
   {busy && <p role="status">{busy}</p>}
   <div className="live-row"><button className="live-primary" data-testid="live-image-save" disabled={!img || !!busy && busy === 'Saving…'} onClick={save}>Save as new asset</button><button onClick={onClose}>Cancel</button></div>
  </div>
 </div>
}
