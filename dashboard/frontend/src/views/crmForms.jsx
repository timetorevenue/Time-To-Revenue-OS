import { useRef, useState } from 'react'
import { postCrmEvent } from '../api'
import { crmEventBody, newRequestKey } from '../commandCentreState'

export function Field({ label, children }) {
  return <label className="cc-field"><span>{label}</span>{children}</label>
}

export function ActionNotice({ state }) {
  if (state.error) return <p className="data-notice warning" role="alert">{state.error}</p>
  if (state.ok) return <p className="data-notice" role="status">{state.ok}</p>
  return null
}

// Keep a retry key until a confirmed response; every edit carries the record revision.
export function useCrmAction(record, onSaved) {
  const [state, setState] = useState({ busy: false, error: '', ok: '' })
  const key = useRef(newRequestKey('crm'))
  const submit = async (kind, payload, basis, sourceRefs, message) => {
    if (state.busy) return false
    setState({ busy: true, error: '', ok: '' })
    try {
      const data = await postCrmEvent(crmEventBody({ kind, recordId: record.id, payload, basis, sourceRefs, requestKey: key.current, expectedRevision: record.revision }))
      key.current = newRequestKey('crm')
      setState({ busy: false, error: '', ok: data.created ? message : 'Already recorded (this retry was not saved twice).' })
      onSaved(data.record)
      return true
    } catch (error) {
      setState({ busy: false, error: error?.response?.data?.detail || error?.message || 'The CRM is unavailable.', ok: '' })
      return false
    }
  }
  return [state, submit]
}
