import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Local dev proxies /api to the Python service so the browser makes
// same-origin requests — identical to how it behaves on Vercel, and no CORS
// configuration is needed to run the stack locally.
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', sourcemap: false },
  server: {
    port: 3000,
    proxy: {
      '/api': { target: process.env.VITE_DEV_API || 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: process.env.VITE_DEV_API || 'http://127.0.0.1:8000', changeOrigin: true },
      '/ready': { target: process.env.VITE_DEV_API || 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
