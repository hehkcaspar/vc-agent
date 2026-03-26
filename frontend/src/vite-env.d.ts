/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Call FastAPI directly (no /api proxy). Example: http://127.0.0.1:8000 */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
