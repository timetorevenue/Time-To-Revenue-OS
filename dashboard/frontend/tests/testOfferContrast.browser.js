import { initialDataFor } from '../src/studio/state.js'
import { designCss } from '../src/studio/design.js'
import { contrastChecks, contrastRatio } from '../src/studio/contrast.js'
import services from '../src/studio/services.json' with { type: 'json' }

export async function mountOffer() {
  const data = initialDataFor('test-offer')
  const css = await (await fetch('/studio/homepage.css')).text()
  const frame = document.createElement('iframe')
  frame.id = 'offer-contrast-evidence'
  frame.style.cssText = 'width:100%;height:2600px;border:0'
  document.body.replaceChildren(frame)
  const doc = frame.contentDocument
  const sections = data.content.map(item => services.pages['test-offer'].sections.find(section => section.type === item.type).html.replace(/<studio-text data-field="([^"]+)"><\/studio-text>/g, (_, field) => `<span data-check="${item.props.id}:${field}">${item.props[field]}</span>`)).join('')
  doc.open()
  doc.write(`<style>${css}\n${designCss(data.root.props.design)}</style><main class="editorial" data-studio-page="test-offer">${sections}</main>`)
  doc.close()
  await doc.fonts.ready
  const evidence = { url: location.origin, page: 'test-offer', records: [], cases: [], provider_calls: 0, model_calls: 0 }
  const hex = value => '#' + value.match(/[\d.]+/g).slice(0, 3).map(number => Number(number).toString(16).padStart(2, '0')).join('')
  const computedCheck = element => {
    const computed = frame.contentWindow.getComputedStyle(element)
    let parent = element, background
    do {
      background = frame.contentWindow.getComputedStyle(parent).backgroundColor
      parent = parent.parentElement
    } while (parent && (background === 'rgba(0, 0, 0, 0)' || background === 'transparent'))
    const foreground = hex(computed.color), bg = hex(background), size = parseFloat(computed.fontSize), weight = Number(computed.fontWeight)
    const required = size >= 24 || (size >= 18.66 && weight >= 700) ? 3 : 4.5
    const ratio = contrastRatio(foreground, bg)
    return { id: element.dataset.check, foreground, background: bg, size, weight, required, ratio, pass: ratio >= required }
  }
  const measure = (state = 'default', modelState = state) => {
    const expected = new Map(contrastChecks(data, modelState).map(check => [check.id, check]))
    const checks = [...doc.querySelectorAll('[data-check]')].map(element => {
      const row = computedCheck(element), model = expected.get(row.id)
      return { ...row, model_colors_match: model.foreground.hex === row.foreground && model.background.hex === row.background }
    })
    evidence.records.push({ width: frame.clientWidth, state, checks })
    if (checks.length !== 26 || checks.some(row => !row.pass || !row.model_colors_match)) throw Error(JSON.stringify(checks.filter(row => !row.pass || !row.model_colors_match)))
    return { width: frame.clientWidth, state, fields: checks.length, min_ratio: Math.min(...checks.map(row => row.ratio)) }
  }
  const fieldCases = () => {
    for (const element of doc.querySelectorAll('[data-check]')) {
      element.style.cssText = 'color:#ffffff;background:#ffffff'
      const violation = computedCheck(element)
      element.style.color = '#111315'
      const compliant = computedCheck(element)
      element.removeAttribute('style')
      if (violation.pass || !compliant.pass) throw Error(`Invalid contrast case ${element.dataset.check}`)
      evidence.cases.push({ width: frame.clientWidth, violation, compliant })
    }
    return evidence.cases.length
  }
  return { frame, doc, evidence, measure, fieldCases }
}
