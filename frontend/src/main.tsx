import React from 'react';
import ReactDOM from 'react-dom/client';
import { PublicClientApplication } from '@azure/msal-browser';
import { MsalProvider } from '@azure/msal-react';
import { msalConfig } from './authConfig';
import { AuthProvider } from './components/AuthContext';
import { App } from './App';
import './index.css';

// MSAL.js v3 requires async initialization before any other API call.
// We create the instance, await initialize(), then render the React tree.
const msalInstance = new PublicClientApplication(msalConfig);

function renderFatalError(message: string, detail?: string) {
  const root = document.getElementById('root');
  if (!root) return;
  root.innerHTML = `
    <div style="height:100vh;display:flex;align-items:center;justify-content:center;background:#0f172a;padding:24px;font-family:'Inter',sans-serif">
      <div style="max-width:560px;background:#1e293b;border:1px solid rgba(239,68,68,0.3);border-radius:12px;padding:32px;box-shadow:0 10px 25px rgba(0,0,0,0.5);text-align:center">
        <h1 style="color:#fff;font-size:20px;font-weight:700;margin:0 0 12px">Fatal Application Error</h1>
        <p style="color:#94a3b8;font-size:14px;line-height:1.5;margin:0 0 12px">${message}</p>
        ${detail ? `<p style="color:#64748b;font-size:12px;margin:0 0 16px;word-break:break-word">${detail}</p>` : ''}
        <p style="color:#64748b;font-size:12px;margin:0">Please refresh the page. If the problem persists, contact support.</p>
      </div>
    </div>
  `;
}

async function bootstrap() {
  try {
    await msalInstance.initialize();
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.error('MSAL initialization failed:', error);
    renderFatalError('Failed to initialize the authentication library.', msg);
    return;
  }

  const rootEl = document.getElementById('root');
  if (!rootEl) {
    console.error('Root element #root not found in DOM.');
    renderFatalError('Application root element not found in the DOM.');
    return;
  }

  ReactDOM.createRoot(rootEl).render(
    <React.StrictMode>
      <MsalProvider instance={msalInstance}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MsalProvider>
    </React.StrictMode>
  );
}

void bootstrap();
