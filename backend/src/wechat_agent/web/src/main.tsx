import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ThemeProvider } from './contexts/ThemeContext'
import { AppearanceProvider } from './contexts/AppearanceContext'
import App from './App'
import ErrorBoundary from './components/ErrorBoundary'
import '../index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ThemeProvider>
        <AppearanceProvider>
          <ErrorBoundary>
            <App />
          </ErrorBoundary>
        </AppearanceProvider>
      </ThemeProvider>
    </BrowserRouter>
  </React.StrictMode>
)
