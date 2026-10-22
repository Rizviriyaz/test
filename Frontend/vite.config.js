import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import process from 'node:process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig(({ mode }) => {
  const fileEnv = loadEnv(mode, process.cwd(), 'VITE_')
  const apiUrl = (process.env.VITE_API_URL ?? fileEnv.VITE_API_URL ?? '').trim()
  // On Vercel the backend service is served same-origin under /api (see vercel.json rewrites),
  // so an unset VITE_API_URL is valid there: fleetApi.js falls back to relative /api/* URLs.
  const sameOriginOnVercel = !apiUrl && Boolean(process.env.VERCEL)

  if (mode === 'production' && !sameOriginOnVercel) {
    if (!apiUrl) {
      throw new Error('VITE_API_URL is required for production builds.')
    }
    let parsedApiUrl
    try {
      parsedApiUrl = new URL(apiUrl)
    } catch {
      throw new Error('VITE_API_URL must be an absolute HTTP(S) URL for production builds.')
    }
    if (!['http:', 'https:'].includes(parsedApiUrl.protocol)) {
      throw new Error('VITE_API_URL must use HTTP or HTTPS for production builds.')
    }
    if (['localhost', '127.0.0.1', '::1'].includes(parsedApiUrl.hostname.toLowerCase())) {
      throw new Error('VITE_API_URL must not use a loopback host in production.')
    }
    process.env.VITE_API_URL = apiUrl
  }

  return {
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    plugins: [
      react(),
      tailwindcss(),
    ],
  }
})
