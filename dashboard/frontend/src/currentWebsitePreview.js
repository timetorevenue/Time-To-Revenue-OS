const textNodes = (doc, root) => {
  const walker = doc.createTreeWalker(root, 4), nodes = []; let node
  while ((node = walker.nextNode())) if (node.textContent.trim() && !node.parentElement.closest('svg,script,style')) nodes.push(node)
  return nodes
}

// Render the published markup/CSS itself. Values enter through DOM setters,
// never through HTML interpolation. Scripts and outbound forms stay disabled.
export function currentPreview(capture, data) {
  const doc = new DOMParser().parseFromString('<!doctype html><html><head></head><body></body></html>', 'text/html')
  doc.documentElement.className = capture.htmlClass
  doc.body.className = capture.bodyClass
  const meta = doc.createElement('meta')
  meta.httpEquiv = 'Content-Security-Policy'
  meta.content = "default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' https://www.core-site.example; font-src https://www.core-site.example; form-action 'none'; base-uri 'none'; frame-src 'none'"
  doc.head.append(meta)
  const viewport = doc.createElement('meta'); viewport.name = 'viewport'; viewport.content = 'width=device-width, initial-scale=1'; doc.head.append(viewport)
  const css = doc.createElement('link'); css.rel = 'stylesheet'; css.href = location.origin + '/studio/current-site.css'; doc.head.append(css)
  const fragment = html => new DOMParser().parseFromString(html, 'text/html').body.firstElementChild
  if (capture.header) doc.body.append(doc.importNode(fragment(capture.header), true))
  const main = doc.createElement('main'); main.className = capture.mainClass
  for (const item of data.sections) {
    const source = capture.sections.find(s => s.id === item.source)
    const el = doc.importNode(fragment(source.html), true)
    el.dataset.currentSection = item.id
    const nodes = textNodes(doc, el)
    source.texts.forEach((field, index) => {
      const node = nodes[index]
      if (!node) return
      if (Object.hasOwn(item.texts, field.id)) node.textContent = item.texts[field.id]
      node.parentElement.dataset.currentField = field.id
    })
    el.querySelectorAll('img').forEach((img, i) => {
      const field = source.images[i]; if (!field) return
      img.dataset.currentImage = field.id
      const value = item.images[field.id]
      if (value) { img.src = new URL(value.src, location.origin).href; img.alt = value.alt }
    })
    for (const [key, value] of Object.entries(item.styles)) {
      el.style[key] = typeof value === 'number' ? value + 'px' : value
      if (['color', 'fontFamily', 'fontSize', 'textAlign'].includes(key)) {
        el.querySelectorAll('h1,h2,h3,h4,p,li,span,a,button').forEach(n => n.style.setProperty(key.replace(/[A-Z]/g, x => '-' + x.toLowerCase()), typeof value === 'number' ? value + 'px' : value, 'important'))
      }
    }
    main.append(el)
  }
  doc.body.append(main)
  if (capture.footer) doc.body.append(doc.importNode(fragment(capture.footer), true))
  // Defense in depth for future recaptures; no active or embedded content.
  doc.querySelectorAll('script,iframe,object,embed,base,foreignObject').forEach(n => n.remove())
  doc.querySelectorAll('*').forEach(n => [...n.attributes].forEach(a => { if (/^on/i.test(a.name) || ['action', 'formaction', 'srcdoc'].includes(a.name)) n.removeAttribute(a.name) }))
  return '<!doctype html>' + doc.documentElement.outerHTML
}
