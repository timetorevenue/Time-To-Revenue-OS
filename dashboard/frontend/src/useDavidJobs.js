import { useCallback, useEffect, useRef, useState } from 'react'
import { getDavidJobs } from './api'
import { jobsFailure } from './jobPresentation'

// One shared read projection for Home, Activity, Jobs and Results. No writes.
export default function useDavidJobs() {
  const [state, setState] = useState({
    jobs: [],
    loading: true,
    error: '',
    fetchedAt: null,
  })
  const alive = useRef(false)
  const pending = useRef(false)
  const refresh = useCallback(async () => {
    if (pending.current) return
    pending.current = true
    try {
      const data = await getDavidJobs()
      if (!Array.isArray(data.jobs)) throw new Error('Invalid jobs response')
      if (alive.current)
        setState({
          jobs: data.jobs,
          loading: false,
          error: '',
          fetchedAt: new Date().toISOString(),
        })
    } catch (error) {
      if (alive.current)
        setState((old) => ({
          ...old,
          loading: false,
          error: jobsFailure(error),
        }))
    } finally {
      pending.current = false
    }
  }, [])
  useEffect(() => {
    alive.current = true
    refresh()
    const poll = window.setInterval(refresh, 15000)
    return () => {
      alive.current = false
      window.clearInterval(poll)
    }
  }, [refresh])
  return { ...state, refresh }
}
