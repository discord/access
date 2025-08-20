import * as React from 'react';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {createRoot} from 'react-dom/client';
import {BrowserRouter} from 'react-router-dom';
import {AdapterDayjs} from '@mui/x-date-pickers/AdapterDayjs';
import {LocalizationProvider} from '@mui/x-date-pickers';
import * as Sentry from '@sentry/react';

import App from './App';
import Error from './pages/Error';

import {appName} from './config/accessConfig';

document.title = appName;
const metaDesc = document.querySelector('meta[name="description"]');
if (metaDesc) metaDesc.setAttribute('content', `${appName}!`);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {retry: false},
  },
});

if (['production', 'staging'].includes(import.meta.env.MODE)) {
  // Use a placeholder DSN as we'll be using the tunnel to proxy all Sentry React errors
  Sentry.init({
    dsn: 'https://user@example.ingest.sentry.io/1234567',
    release: import.meta.env.VITE_SENTRY_RELEASE,
    integrations: [Sentry.replayIntegration()],
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 1.0,
    tunnel: '/api/bugs/sentry',
  });
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Sentry.ErrorBoundary fallback={<Error />} showDialog>
      <BrowserRouter>
        <QueryClientProvider client={queryClient}>
          <LocalizationProvider dateAdapter={AdapterDayjs}>
            <App />
          </LocalizationProvider>
        </QueryClientProvider>
      </BrowserRouter>
    </Sentry.ErrorBoundary>
  </React.StrictMode>,
);
