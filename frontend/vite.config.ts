import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API stays same-origin in dev, so the backend needs no CORS.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://localhost:8000' } },
})
