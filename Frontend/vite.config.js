import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import process from 'node:process'

export default defineConfig(({ mode }) => {
  if (mode === 'production') {
    const fileEnv = loadEnv(mode, process.cwd(), 'VITE_')
    const apiUrl = (process.env.VITE_API_URL ?? fileEnv.VITE_API_URL ?? '').trim()
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
    plugins: [
      react(),
      tailwindcss(),
    ],
  }
})
