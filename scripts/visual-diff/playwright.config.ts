/*
 * Playwright config for the visual diff. `run.js` invokes it once per side with the
 * VISUAL_DIFF_* environment variables set; it is not meant to be run on its own.
 */
import path from 'node:path';
import {defineConfig, devices} from '@playwright/test';

const side = process.env.VISUAL_DIFF_SIDE ?? 'head';
const outDir = process.env.VISUAL_DIFF_OUT_DIR ?? path.resolve(import.meta.dirname, '../../.visual-diff');

export default defineConfig({
  testDir: import.meta.dirname,
  testMatch: 'pages.spec.ts',
  outputDir: path.join(outDir, `test-results-${side}`),
  // Both sides write to and read from one directory, so the base run's screenshots become the
  // head run's expectations without anything ever being checked in.
  snapshotPathTemplate: path.join(outDir, 'snapshots', '{arg}{ext}'),
  fullyParallel: true,
  workers: 4,
  retries: 0,
  timeout: 60_000,
  reporter:
    side === 'head' ? [['line'], ['html', {outputFolder: path.join(outDir, 'report'), open: 'never'}]] : [['line']],
  expect: {
    // Two launches of the same browser on the same page still antialias a few glyphs a shade
    // differently (up to about 50 pixels on a page here), so a handful of differing pixels is
    // treated as noise. A real change to layout, spacing, or color moves far more than this.
    toHaveScreenshot: {animations: 'disabled', caret: 'hide', scale: 'css', maxDiffPixels: 100},
  },
  use: {
    baseURL: process.env.VISUAL_DIFF_BASE_URL,
    locale: 'en-US',
    timezoneId: 'UTC',
    colorScheme: 'light',
    contextOptions: {reducedMotion: 'reduce'},
    trace: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: {width: 1440, height: 900},
        deviceScaleFactor: 1,
        // Software rendering with hinting and subpixel text off, so the same page renders to the
        // same pixels from one run to the next.
        launchOptions: {
          args: [
            '--disable-gpu',
            '--force-color-profile=srgb',
            '--font-render-hinting=none',
            '--disable-lcd-text',
            '--disable-font-subpixel-positioning',
          ],
        },
      },
    },
  ],
});
