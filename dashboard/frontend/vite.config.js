import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { viteCacheDir } from './vite.cache.js'

const root = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig({
  define: { __TTROS_SITE__: JSON.stringify({ id: process.env.TTROS_STUDIO_SITE_ID || '', domain: process.env.TTROS_SITE_DOMAIN || '' }) },
  // Production (3010) keeps node_modules/.vite; every other server gets an isolated cache.
  cacheDir: viteCacheDir({ root }),
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 3010,
    // Tailscale Serve (tailnet only) proxies this hostname to 127.0.0.1:3010.
    allowedHosts: ['node.tailnet.example'],
    proxy: {
      '/api/website/studio': { target: process.env.TTROS_STUDIO_API_TARGET || 'http://127.0.0.1:8010', changeOrigin: true },
      '/api': {
        target: process.env.TTROS_API_TARGET || 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
    },
  },
})
