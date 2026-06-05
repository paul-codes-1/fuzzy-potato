import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'
import { loadSiteConfig } from './config/site'

// Fetch the jurisdiction's /data/site.json before the first render so every
// component can read getSiteConfig() synchronously. Falls back to the baked-in
// LFUCG defaults if the fetch fails or the file is absent.
loadSiteConfig().finally(() => {
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </React.StrictMode>
  )
})
