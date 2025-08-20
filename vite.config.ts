/// <reference types="vitest" />
import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {sentryVitePlugin} from '@sentry/vite-plugin';
import path from 'path';
import {loadAccessConfig} from './src/config/loadAccessConfig';

const accessConfig = loadAccessConfig();

export default defineConfig({
  plugins: [
    react(),
    // Only include Sentry plugin in production builds
    ...(process.env.NODE_ENV === 'production' && !!process.env.SENTRY_AUTH_TOKEN
      ? [
          sentryVitePlugin({
            org: process.env.SENTRY_ORG,
            project: process.env.SENTRY_PROJECT,
            authToken: process.env.SENTRY_AUTH_TOKEN,
            sourcemaps: {
              assets: './build/**',
              filesToDeleteAfterUpload: './build/**/*.map',
            },
            release: {
              name: process.env.SENTRY_RELEASE,
            },
          }),
        ]
      : []),
  ],
  resolve: {
    alias: {
      '@mui/styled-engine': '@mui/styled-engine-sc',
    },
  },
  define: {
    ACCESS_CONFIG: accessConfig,
    APP_NAME: JSON.stringify(process.env.APP_NAME || 'Access'),
  },
  server: {
    port: 3000,
  },
  build: {
    outDir: 'build',
    sourcemap:
      process.env.NODE_ENV === 'development' ||
      (process.env.NODE_ENV === 'production' && !!process.env.SENTRY_AUTH_TOKEN), // Enable source maps for Sentry
  },
  publicDir: 'public',
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/setupTests.ts',
  },
});
