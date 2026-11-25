import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Frontend never talks to Sarvam/Flagsmith/NudgeBee: everything goes through our backend.
const api = 'http://localhost:8000'
const paths = ['/sessions', '/config', '/answer', '/positions', '/tts', '/ready', '/admin']
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, proxy: Object.fromEntries(paths.map((p) => [p, api])) },
})
