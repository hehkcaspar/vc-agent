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
  }
})
