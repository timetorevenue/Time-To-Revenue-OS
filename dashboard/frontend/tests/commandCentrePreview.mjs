// Isolated UI validation only. No imports of runtime code, writes or real API proxy.
// Run: node tests/commandCentrePreview.mjs (port 4311).
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
const receipt = {
  path: 'queue/receipts/AOS-2026-9001.md',
  status: 'done',
  created_at: '2026-11-15T08:00:00Z',
  sha256: 'a'.repeat(64),
}
const jobs = [
  {
    id: 'AOS-2026-9001',
    status: 'done',
    topic: 'Fixture evidence',
    request: 'Fixture: receipt-backed result',
    receipts: [receipt],
    result: {
      summary: 'Isolated fixture result. No business work was executed.',
    },
    artifacts: [{ path: 'results/fixture-only.md', sha256: 'b'.repeat(64) }],
  },
  {
    id: 'AOS-2026-9002',
    status: 'running',
    topic: 'Fixture work',
    request: 'Fixture: work in motion',
  },
  {
    id: 'AOS-2026-9003',
    status: 'needs_input',
    topic: 'Fixture attention',
    request: 'Fixture: a question needs your input',
  },
  {
    id: 'AOS-2026-9004',
    status: 'done',
    topic: 'Fixture incomplete evidence',
    request: 'Fixture: done without a receipt',
  },
  {
    id: 'AOS-2026-9005',
    status: 'uncertain',
    topic: 'Fixture recovery',
    request: 'Fixture: uncertain outcome',
  },
].map((job) => ({
  created_at: '2026-11-15T07:00:00Z',
  conversation_id: 'fixture-dashboard',
  ...job,
}))
let mode = 'normal'
const calls = []
const server = await createServer({
  configFile: false,
  plugins: [
    react(),
    {
      name: 'isolated-command-centre-fixtures',
      transformIndexHtml(html) {
        return html.replace(
          '<body>',
          '<body><div style="position:fixed;top:0;left:50%;transform:translateX(-50%);z-index:1000;background:#fff0cb;color:#614512;font:10px monospace;padding:5px 12px;pointer-events:none;white-space:nowrap">ISOLATED VALIDATION FIXTURES · NOT LIVE DATA</div>',
        )
      },
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          const url = new URL(req.url, 'http://fixture')
          const json = (data, status = 200) => {
            res.statusCode = status
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify(data))
          }
          if (url.pathname === '/__fixture') {
            if (url.searchParams.has('mode'))
              mode = url.searchParams.get('mode')
            return json({ mode, calls })
          }
          if (!url.pathname.startsWith('/api/')) return next()
          calls.push({ method: req.method, path: url.pathname })
          if (url.pathname === '/api/health')
            return json({ status: 'ok', david: { ready: true } })
          if (url.pathname === '/api/david/jobs') {
            if (mode === 'error')
              return json({ detail: 'Fixture connection failure' }, 503)
            if (mode === 'unavailable')
              return json({ detail: 'Fixture unsupported route' }, 404)
            const send = () => json({ jobs: mode === 'empty' ? [] : jobs })
            return mode === 'loading' ? setTimeout(send, 1800) : send()
          }
          if (url.pathname.startsWith('/api/david/jobs/')) {
            const job = jobs.find(
              (job) =>
                job.id === decodeURIComponent(url.pathname.split('/').at(-1)),
            )
            return json(
              job || { detail: 'Unknown fixture ID' },
              job ? 200 : 404,
            )
          }
          if (
            url.pathname === '/api/dashboard/ask-david' &&
            req.method === 'POST'
          ) {
            let body = ''
            req.on('data', (chunk) => (body += chunk))
            req.on('end', () => {
              calls.push({ submitted: JSON.parse(body) })
              json({ kind: 'david_job', job_id: 'fixture-turn-pending' })
            })
            return
          }
          if (
            url.pathname ===
            '/api/dashboard/ask-david/jobs/fixture-turn-pending'
          )
            return setTimeout(
              () =>
                json({
                  status: 'done',
                  result: {
                    kind: 'david_reply',
                    success: true,
                    response:
                      'Fixture delegation acknowledged. Follow the job for its actual outcome.',
                    topic: 'Fixture work',
                    job_id: 'AOS-2026-9002',
                  },
                }),
              800,
            )
          return json({ detail: 'Unavailable in isolated fixture server' }, 404)
        })
      },
    },
  ],
  server: { host: '127.0.0.1', port: 4311, strictPort: true },
})
await server.listen()
console.log('Isolated validation fixtures: http://127.0.0.1:4311/home')
