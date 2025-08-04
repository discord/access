/// <reference types="vitest" />
import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {loadAccessConfig} from './src/config/loadAccessConfig';

const accessConfig = loadAccessConfig();

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@mui/styled-engine': '@mui/styled-engine-sc',
    },
  },
  define: {
    ACCESS_CONFIG: accessConfig,
  },
  server: {
    port: 3000,
  },
  build: {
    outDir: 'build',
  },
  publicDir: 'public',
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/setupTests.ts',
  },
});
