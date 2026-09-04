/// <reference types="vitest" />
import {readFileSync} from 'node:fs';
import {defineConfig, loadEnv} from 'vite';
import react from '@vitejs/plugin-react';
import {sentryVitePlugin} from '@sentry/vite-plugin';
import {loadAccessConfig} from './src/config/loadAccessConfig.js';

const accessConfig = loadAccessConfig();

const PORT_FILE = '.claude/.api-port';
const DEFAULT_BACKEND_PORT = 6060;

// Resolves the backend port from .claude/.api-port (written by the Makefile),
// then BACKEND_PORT, then the default. Read at Vite startup, so the
// backend should be started before the frontend in Claude Code Desktop
// Preview; restart Vite if the backend port changes.
function resolveBackendPort(): number {
  try {
    const fromFile = parseInt(readFileSync(PORT_FILE, 'utf8').trim(), 10);
    if (Number.isFinite(fromFile) && fromFile > 0) return fromFile;
  } catch {
    // fall through
  }
  const fromEnv = parseInt(process.env.BACKEND_PORT ?? '', 10);
  if (Number.isFinite(fromEnv) && fromEnv > 0) return fromEnv;
  return DEFAULT_BACKEND_PORT;
}

export default defineConfig(({mode}) => {
  // Load env file based on `mode` in the current working directory.
  // Process environment variables take precedence over .env files
  const env = {...loadEnv(mode, process.cwd(), ''), ...process.env};

  return {
    plugins: [
      react(),
      // Vite 7's dev server does not expose `define` values as runtime globals the way the app
      // reads them (ACCESS_CONFIG / APP_NAME / REQUIRE_DESCRIPTIONS), so under `vite dev` those
      // identifiers are undefined and the SPA throws on boot (blank screen). Inject them as window
      // globals for the dev server only; production builds still get them via the `define` static
      // replacement below.
      {
        name: 'inject-access-config-dev',
        apply: 'serve',
        transformIndexHtml: () => [
          {
            tag: 'script',
            injectTo: 'head-prepend',
            children:
              `window.ACCESS_CONFIG=${accessConfig};` +
              `window.APP_NAME=${JSON.stringify(env.APP_NAME || 'Access')};` +
              `window.REQUIRE_DESCRIPTIONS=${JSON.stringify(env.REQUIRE_DESCRIPTIONS?.toLowerCase() === 'true')};`,
          },
        ],
      },
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
      // Test-only field order. Vitest resolves bare dependencies through Node, which
      // picks MUI's CJS `main` (`node/index.js`); that build `require`s
      // '@mui/styled-engine' itself, bypassing the alias above and demanding
      // @emotion/styled, which this app does not install (it renders through
      // styled-components). Preferring `module` keeps MUI on its ESM build so the
      // alias applies and component tests can render real MUI instead of mocking it.
      // The app build keeps Vite's default order.
      ...(mode === 'test' ? {mainFields: ['module', 'browser', 'main']} : {}),
    },
    define: {
      ACCESS_CONFIG: accessConfig,
      APP_NAME: JSON.stringify(env.APP_NAME || 'Access'),
      REQUIRE_DESCRIPTIONS: env.REQUIRE_DESCRIPTIONS?.toLowerCase() === 'true',
    },
    server: {
      port: 3000,
      proxy: {
        '/api': {
          target: `http://localhost:${resolveBackendPort()}`,
          changeOrigin: true,
          secure: false,
        },
      },
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
      // Scope discovery to src/ instead of Vitest's default project-wide glob.
      // The default walks every directory under the repo root, and its exclude
      // list covers only node_modules and .git -- so a nested checkout (a git
      // worktree, a vendored copy) has its test files collected and run against
      // *this* checkout's node_modules, reporting failures that belong to some
      // other branch. Every frontend test lives under src/.
      include: ['src/**/*.{test,spec}.?(c|m)[jt]s?(x)'],
      // Inline MUI and react-hook-form-mui so Vite transforms them (and applies the
      // styled-engine alias) rather than handing them to Node's CJS loader.
      server: {deps: {inline: [/@mui/, /react-hook-form-mui/]}},
    },
  };
});
