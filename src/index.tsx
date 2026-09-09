import * as React from 'react';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {createRoot} from 'react-dom/client';
import {BrowserRouter} from 'react-router-dom';
import {AdapterDayjs} from '@mui/x-date-pickers/AdapterDayjs';
import {LocalizationProvider} from '@mui/x-date-pickers';
import createCache from '@emotion/cache';
import {CacheProvider} from '@emotion/react';
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

// Cache plugin metadata indefinitely since it doesn't change while the app is running
queryClient.setQueryDefaults(['api', 'plugins'], {
  staleTime: Infinity,
  gcTime: Infinity,
  refetchOnMount: false,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
});

if (['production', 'staging'].includes(import.meta.env.MODE)) {
  // Use a placeholder DSN as we'll be using the tunnel to proxy all Sentry React errors
  Sentry.init({
    dsn: 'https://user@example.ingest.sentry.io/1234567',
    release: import.meta.env.VITE_SENTRY_RELEASE,
    integrations: [Sentry.replayIntegration()],
    // React Query cancels in-flight requests when the last observer goes away, which
    // aborts their signal. That is the intended behaviour, not a fault, and the resulting
    // rejection reports with no application frames in it. Matched on the exception type
    // so genuine failures still come through.
    ignoreErrors: ['AbortError'],
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 1.0,
    tunnel: '/api/bugs/sentry',
  });
}

// Every MUI component styles itself by injecting a `<style>` element at runtime, and the
// production `style-src` admits inline styles only by nonce (see `build_csp` in
// api/middleware.py). Emotion stamps the nonce on the elements it inserts, but only when the
// cache is built with one, so this provider is what stands between the app and an entirely
// unstyled page. `api.app.serve_spa` publishes the per-response nonce as
// `window.__webpack_nonce__`; it is absent under `vite dev`, whose relaxed policy carries
// 'unsafe-inline' instead.
const emotionCache = createCache({key: 'css', nonce: window.__webpack_nonce__});

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <CacheProvider value={emotionCache}>
      <Sentry.ErrorBoundary fallback={<Error />} showDialog>
        <BrowserRouter>
          <QueryClientProvider client={queryClient}>
            <LocalizationProvider dateAdapter={AdapterDayjs}>
              <App />
            </LocalizationProvider>
          </QueryClientProvider>
        </BrowserRouter>
      </Sentry.ErrorBoundary>
    </CacheProvider>
  </React.StrictMode>,
);
