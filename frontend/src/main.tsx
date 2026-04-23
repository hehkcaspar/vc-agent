// Side-effect import — installs the window.fetch shim that attaches the
// shared-password header. Must precede any module that captures fetch.
import './services/auth'

import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { LoginGate } from './components/LoginGate'
import './styles/global.css'
import './styles/primitives.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <LoginGate>
      <App />
    </LoginGate>
  </React.StrictMode>,
)
