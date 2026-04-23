import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { ViewportGuard } from './components/ViewportGuard'
import './styles/global.css'
import './styles/primitives.css'

// Apply the stored (or system-preferred) theme synchronously before React
// paints. Layout's useEffect later takes over as the authoritative controller
// on toggle, but doing this early means any pre-Layout screen — the
// ViewportGuard denial, a SSR/hydration flash — starts with the right theme.
const savedTheme = localStorage.getItem('theme');
const initialTheme =
  savedTheme === 'light' || savedTheme === 'dark'
    ? savedTheme
    : window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark'
      : 'light';
document.documentElement.setAttribute('data-theme', initialTheme);

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ViewportGuard>
      <BrowserRouter
        future={{
          v7_startTransition: true,
          v7_relativeSplatPath: true,
        }}
      >
        <App />
      </BrowserRouter>
    </ViewportGuard>
  </React.StrictMode>,
)
