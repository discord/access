/// <reference types="vitest" />
import {defineConfig, loadEnv} from 'vite';
import react from '@vitejs/plugin-react';
import {sentryVitePlugin} from '@sentry/vite-plugin';
import {loadAccessConfig} from './src/config/loadAccessConfig';

const accessConfig = loadAccessConfig();

export default defineConfig(({mode}) => {
  // Load env file based on `mode` in the current working directory.
  // Process environment variables take precedence over .env files
  const env = {...loadEnv(mode, process.cwd(), ''), ...process.env};

  return {
    plugins: [
      react(),
      // Only include Sentry plugin in production builds
      ...(env.NODE_ENV === 'production' && !!env.SENTRY_AUTH_TOKEN
        ? [
            sentryVitePlugin({
              org: env.SENTRY_ORG,
              project: env.SENTRY_PROJECT,
              authToken: env.SENTRY_AUTH_TOKEN,
              sourcemaps: {
                assets: './build/**',
                filesToDeleteAfterUpload: './build/**/*.map',
              },
              release: {
                name: env.SENTRY_RELEASE,
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
      APP_NAME: JSON.stringify(env.APP_NAME || 'Access'),
      REQUIRE_DESCRIPTIONS: env.REQUIRE_DESCRIPTIONS?.toLowerCase() === 'true',
    },
    server: {
      port: 3000,
    },
    build: {
      outDir: 'build',
      sourcemap: env.NODE_ENV === 'development' || (env.NODE_ENV === 'production' && !!env.SENTRY_AUTH_TOKEN), // Enable source maps for Sentry
    },
    publicDir: 'public',
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: './src/setupTests.ts',
    },
  };
});
