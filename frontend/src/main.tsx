import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
// Self-hosted fonts (variable-weight Manrope + JetBrains Mono), bundled from
// node_modules by Vite rather than fetched from Google Fonts at runtime — see
// src/index.css's top comment. Each package's index.css declares one
// @font-face per Unicode subset (latin, latin-ext, cyrillic, greek, …); a
// browser only ever downloads the woff2 for the subset it actually needs
// (that's what @font-face's unicode-range is for), so importing the whole
// index.css costs nothing at runtime beyond a little metadata text — there
// is no separate "latin-only" build to import instead. Variable builds
// specifically because the type scale uses in-between weights (650) that
// only a continuous weight axis can render exactly.
import '@fontsource-variable/manrope'
import '@fontsource-variable/jetbrains-mono'
import './index.css'
import App from './App.tsx'
import { ThemeModeProvider } from './theme/ThemeModeProvider'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeModeProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ThemeModeProvider>
  </StrictMode>,
)
