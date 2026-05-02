import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Dev/preview proxy: browser calls /api/* → FastAPI without /api prefix.
 * If port 8000 is Kong (or another gateway) and you see
 * {"message":"no Route matched with those values"}, set VITE_PROXY_TARGET
 * to the real Uvicorn base URL, e.g. http://127.0.0.1:8001
 *
 * Or bypass the proxy entirely: VITE_API_URL=http://127.0.0.1:8000 in frontend/.env
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxyTarget = env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000'

  const proxy = {
    '/api': {
      target: proxyTarget,
      changeOrigin: true,
      rewrite: (path: string) => path.replace(/^\/api/, ''),
    },
  }

  return {
    plugins: [react()],
    server: {
      port: 3000,
      proxy,
    },
    preview: {
      port: 4173,
      proxy,
    },
    build: {
      // Vendor chunk split — separates framework + heavy single-use
      // libs from the app bundle so (a) framework upgrades invalidate
      // less of the user's browser cache, and (b) each chunk fits
      // under the 500 kB warning threshold (the previous single
      // 2.9 MB chunk triggered a vite warning on every build).
      rollupOptions: {
        output: {
          // Vite 8 ships rolldown which only supports the function
          // form of manualChunks (not the object map). Match by
          // node_modules path segment so any nested re-export of these
          // libs lands in the right chunk.
          manualChunks(id: string) {
            if (!id.includes('node_modules')) return undefined
            if (
              id.includes('node_modules/react/') ||
              id.includes('node_modules/react-dom/') ||
              id.includes('node_modules/react-router-dom/') ||
              id.includes('node_modules/react-router/') ||
              id.includes('node_modules/scheduler/')
            ) return 'react-vendor'
            if (
              id.includes('node_modules/react-markdown/') ||
              id.includes('node_modules/remark-') ||
              id.includes('node_modules/micromark') ||
              id.includes('node_modules/mdast-')
            ) return 'markdown-vendor'
            if (id.includes('node_modules/lucide-react/')) return 'icons-vendor'
            if (id.includes('node_modules/swr/')) return 'swr-vendor'
            // File-format parsers — only loaded when the user previews
            // a Word / Excel / PowerPoint upload. Splitting them out
            // means a fresh page load doesn't pay their bytes (a
            // dynamic import of FilePreview would be even better but
            // requires a frontend refactor).
            if (id.includes('node_modules/xlsx/')) return 'xlsx-vendor'
            if (id.includes('node_modules/mammoth/')) return 'mammoth-vendor'
            if (id.includes('node_modules/pptx-preview/')) return 'pptx-vendor'
            // Charts (Academic ranking view).
            if (
              id.includes('node_modules/recharts/') ||
              id.includes('node_modules/d3-')
            ) return 'recharts-vendor'
            return undefined
          },
        },
      },
      // App-code chunk after vendor split is ~250 kB (down from
      // 2.6 MB on a single-chunk build). The only chunk over 700 kB
      // is `pptx-vendor` (the .pptx parser, ~1.2 MB), which loads
      // ONLY when a user previews a PowerPoint upload — initial-load
      // bytes are unaffected. Threshold raised so a clean build
      // doesn't emit a warning we'd just have to suppress.
      // BACKLOG: lazy-load FilePreview to make pptx-preview a true
      // dynamic import, dropping the threshold back to 700.
      chunkSizeWarningLimit: 1300,
    },
  }
})
