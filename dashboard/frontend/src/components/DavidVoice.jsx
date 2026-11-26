import { useEffect, useRef, useState } from 'react'
import { Mic, Square, Volume2 } from 'lucide-react'
import { speakDavidReply, transcribeDavidRecording } from '../api'
import { ActionButton } from './DashboardKit'

const FORMATS = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus']
const stopTracks = stream => stream?.getTracks().forEach(track => track.stop())

export function DavidMic({ disabled, onTranscript }) {
  const [phase, setPhase] = useState('idle')
  const [notice, setNotice] = useState('')
  const session = useRef(null)

  useEffect(() => () => {
    const current = session.current
    session.current = null
    if (!current) return
    clearTimeout(current.timer)
    current.controller.abort()
    stopTracks(current.stream)
  }, [])

  const toggle = async () => {
    if (session.current) {
      const recorder = session.current.recorder
      if (recorder?.state === 'recording') recorder.stop()
      return
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setNotice('Recording is unavailable in this browser. You can still type your message.')
      return
    }
    const current = { controller: new AbortController() }
    session.current = current
    setNotice('')
    setPhase('starting')

    const finish = () => {
      clearTimeout(current.timer)
      stopTracks(current.stream)
      if (session.current !== current) return false
      session.current = null
      setPhase('idle')
      return true
    }

    try {
      current.stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (session.current !== current) {
        stopTracks(current.stream)
        return
      }
      const mimeType = FORMATS.find(type => MediaRecorder.isTypeSupported?.(type))
      const recorder = new MediaRecorder(current.stream, mimeType ? { mimeType } : {})
      current.recorder = recorder
      const chunks = []
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data) }
      recorder.onerror = () => {
        if (finish()) setNotice('Recording failed. Try again or type your message.')
      }
      recorder.onstop = async () => {
        clearTimeout(current.timer)
        stopTracks(current.stream)
        if (session.current !== current) return
        setPhase('transcribing')
        try {
          const audio = new Blob(chunks, { type: recorder.mimeType || chunks[0]?.type || '' })
          const transcript = await transcribeDavidRecording(audio, current.controller.signal)
          if (finish()) {
            onTranscript(transcript)
            setNotice('Transcript added. Edit it before sending.')
          }
        } catch (error) {
          if (finish()) setNotice(error?.response?.data?.detail || 'Transcription failed. Try again or type your message.')
        }
      }
      recorder.start()
      setPhase('recording')
      current.timer = setTimeout(() => { if (recorder.state === 'recording') recorder.stop() }, 90_000)
    } catch {
      if (finish()) setNotice('Microphone unavailable. Check browser permissions or type your message.')
    }
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <ActionButton type="button" onClick={toggle}
        disabled={phase === 'transcribing' || phase === 'starting' || (disabled && phase === 'idle')}
        aria-label={phase === 'recording' ? 'Stop recording' : 'Record microphone'}
        aria-pressed={phase === 'recording'}>
        {phase === 'recording' ? <Square size={13} /> : <Mic size={13} />}
        {phase === 'recording' ? 'Stop' : phase === 'transcribing' ? 'Transcribing…' : 'Record'}
      </ActionButton>
      {notice && <span className="text-xs text-taupe" role="status">{notice}</span>}
    </span>
  )
}

export function ListenToDavid({ text }) {
  const [audio, setAudio] = useState('')
  const [status, setStatus] = useState('')
  const request = useRef(null)
  const audioUrl = useRef('')

  useEffect(() => () => {
    request.current?.abort()
    if (audioUrl.current) URL.revokeObjectURL(audioUrl.current)
  }, [])

  const listen = async () => {
    if (request.current) return
    const controller = new AbortController()
    request.current = controller
    setStatus('Preparing audio…')
    try {
      const blob = await speakDavidReply(text, controller.signal)
      if (controller.signal.aborted) return
      audioUrl.current = URL.createObjectURL(blob)
      setAudio(audioUrl.current)
      setStatus('')
    } catch {
      if (!controller.signal.aborted) setStatus('Spoken playback is unavailable. The written reply is above.')
    } finally {
      request.current = null
    }
  }

  return audio ? <audio className="mt-2 max-w-full" controls autoPlay src={audio} aria-label="David’s spoken reply" /> : (
    <div className="mt-2 flex items-center gap-2">
      <ActionButton type="button" onClick={listen} disabled={status === 'Preparing audio…'}>
        <Volume2 size={13} />Listen
      </ActionButton>
      {status && <span className="text-xs text-taupe" role="status">{status}</span>}
    </div>
  )
}
