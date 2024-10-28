import * as React from 'react';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {createRoot} from 'react-dom/client';
import {BrowserRouter} from 'react-router-dom';
import {ThemeProvider, createTheme} from '@mui/material/styles';
import CssBaseline from '@mui/material/CssBaseline';
import {AdapterDayjs} from '@mui/x-date-pickers/AdapterDayjs';
import {LocalizationProvider} from '@mui/x-date-pickers';
import * as Sentry from '@sentry/react';

import App from './App';
import Error from './pages/Error';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {retry: false},
  },
});

declare module '@mui/material/styles' {
  interface Palette {
    primary_extra_light: Palette['primary'];
  }

  interface PaletteOptions {
    primary_extra_light?: PaletteOptions['primary'];
  }
}

declare module '@mui/material/Button' {
  interface ButtonPropsColorOverrides {
    primary_extra_light: true;
  }
}

if (['production', 'staging'].includes(process.env.NODE_ENV)) {
  // Use a placeholder DSN as we'll be using the tunnel to proxy all Sentry React errors
  Sentry.init({
    dsn: 'https://user@example.ingest.sentry.io/1234567',
    release: process.env.REACT_APP_SENTRY_RELEASE,
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
