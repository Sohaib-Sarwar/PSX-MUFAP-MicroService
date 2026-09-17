import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `base` decides what every asset URL and `import.meta.env.BASE_URL` resolve
// to. A GitHub Pages project site is served from /<repo>/, not from the root,
// so the workflow passes VITE_BASE and the same source builds correctly for
// either layout without a hardcoded path.
const base = process.env.VITE_BASE || '/'

export default defineConfig({
  base,
  plugins: [react()],
  build: {
    outDir: 'dist',
    sourcemap: false,
    // react-icons pulls in one module per icon; letting them land in the main
    // chunk alongside React makes a single large file that blocks first paint.
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
          icons: ['react-icons/fi'],
        },
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      // Only used when VITE_API_BASE points at a running FastAPI instance.
      // The default dashboard reads the static tree under public/api instead.
      '/health': { target: process.env.VITE_DEV_API || 'http://127.0.0.1:8000', changeOrigin: true },
      '/ready': { target: process.env.VITE_DEV_API || 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
