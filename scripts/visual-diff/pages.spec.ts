/*
 * Visits every route in src/App.tsx, records what went wrong while it rendered, and takes a
 * full-page screenshot. `run.js` runs this one spec against the merge base (writing the
 * screenshots) and then against the current branch (comparing against them), so a route the
 * branch adds shows up as a new page rather than a failure.
 *
 * Each test writes a JSON record to VISUAL_DIFF_RESULTS_DIR; `run.js` diffs the two sides'
 * records to find errors that only the current branch has.
 */
import fs from 'node:fs';
import path from 'node:path';
import {expect, test, type Page} from '@playwright/test';

const fixtures = JSON.parse(fs.readFileSync(path.join(import.meta.dirname, 'fixtures.json'), 'utf8'));
const {users, apps, groups, tags, requests} = fixtures;

const side = process.env.VISUAL_DIFF_SIDE ?? 'head';
const resultsDir =
  process.env.VISUAL_DIFF_RESULTS_DIR ?? path.join(import.meta.dirname, '../../.visual-diff/results', side);
// The seed's timestamps are offsets from the anchor; freezing the browser clock at a fixed offset
// from the same anchor makes every relative date ("3 days ago") identical on both sides.
const frozenNow = new Date(
  new Date(process.env.VISUAL_DIFF_ANCHOR ?? '2026-01-01T00:00:00Z').getTime() + 12 * 3600_000,
);

interface PageCase {
  /** The `<Route path>` this case renders; used to check that every route is covered. */
  route: string;
  url: string;
  /** Screenshot and record name; defaults to a slug of `url`'s route. */
  name?: string;
  /** The catch-all route, where the not-found page is the expected render. */
  expectNotFound?: boolean;
}

const PAGES: PageCase[] = [
  {route: '/', url: '/', name: 'home'},
  {route: '/users', url: '/users'},
  {route: '/users/:id', url: '/users/@me', name: 'users-me'},
  {route: '/users/:id', url: `/users/${users.owner}`},
  {route: '/users/:id/audit', url: `/users/${users.owner}/audit`},
  {route: '/groups', url: '/groups'},
  {route: '/groups/:id', url: `/groups/${groups.engineering}`},
  {route: '/groups/:id', url: `/groups/${groups.wikiEditors}`, name: 'groups-id-app-group'},
  {route: '/groups/:id/audit', url: `/groups/${groups.engineering}/audit`},
  {route: '/roles', url: '/roles'},
  {route: '/roles/:id', url: `/roles/${groups.role}`},
  {route: '/roles/:id/audit', url: `/roles/${groups.role}/audit`},
  {route: '/apps', url: '/apps'},
  {route: '/apps/:id', url: `/apps/${apps.wiki}`},
  {route: '/requests', url: '/requests'},
  {route: '/requests/:id', url: `/requests/${requests.access}`},
  {route: '/expiring-groups', url: '/expiring-groups'},
  {route: '/expiring-roles', url: '/expiring-roles'},
  {route: '/tags', url: '/tags'},
  {route: '/tags/:id', url: `/tags/${tags.sox}`},
  {route: '/role-requests', url: '/role-requests'},
  {route: '/role-requests/:id', url: `/role-requests/${requests.role}`},
  {route: '/group-requests', url: '/group-requests'},
  {route: '/group-requests/:id', url: `/group-requests/${requests.group}`},
  {route: '*', url: '/visual-diff-unknown-page', name: 'not-found', expectNotFound: true},
];

/** Names a case by its route pattern, so a screenshot keeps its name when seed ids change. */
function caseName(c: PageCase): string {
  return c.name ?? c.route.replace(/^\//, '').replace(/:/g, '').replace(/\//g, '-');
}

const MAX_SCREENSHOT_HEIGHT = 10_000;

type ScreenshotOutcome = 'same' | 'changed' | 'new' | 'skipped';

interface PageRecord {
  name: string;
  url: string;
  errors: string[];
  screenshot: ScreenshotOutcome;
}

function writeRecord(record: PageRecord): void {
  fs.mkdirSync(resultsDir, {recursive: true});
  fs.writeFileSync(path.join(resultsDir, `${record.name}.json`), JSON.stringify(record, null, 2));
}

/** Collects uncaught errors, console.error output, and failed API calls for one page. */
function watchForErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(`uncaught: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`console.error: ${message.text()}`);
  });
  const apiPath = (url: string) => {
    const parsed = new URL(url);
    return parsed.pathname.startsWith('/api/') ? parsed.pathname + parsed.search : null;
  };
  page.on('response', (response) => {
    const p = apiPath(response.url());
    if (p && response.status() >= 400) errors.push(`api: ${response.request().method()} ${p} -> ${response.status()}`);
  });
  page.on('requestfailed', (request) => {
    const p = apiPath(request.url());
    // React's development-mode double mount cancels and reissues queries; a cancelled request
    // is not a failure.
    if (p && request.failure()?.errorText !== 'net::ERR_ABORTED')
      errors.push(`api: ${request.method()} ${p} failed (${request.failure()?.errorText})`);
  });
  return errors;
}

/**
 * Turns off CSS transitions and animations before the app mounts. A transition that has merely
 * finished can leave an element on its own compositor layer, which antialiases text a shade
 * differently from one run to the next.
 */
function disableMotion(): void {
  document.addEventListener('DOMContentLoaded', () => {
    const style = document.createElement('style');
    style.textContent =
      '*, *::before, *::after { transition: none !important; animation: none !important; caret-color: transparent !important; }';
    document.head.appendChild(style);
  });
}

/**
 * Grows the viewport to fit the page. The app scrolls inside its <main> element rather than the
 * document, so a full-page screenshot alone would stop at the first screenful.
 */
async function fitViewportToContent(page: Page): Promise<void> {
  const width = page.viewportSize()?.width ?? 1440;
  for (let i = 0; i < 2; i++) {
    const height = await page.evaluate(() =>
      Math.max(document.documentElement.scrollHeight, document.querySelector('main')?.scrollHeight ?? 0),
    );
    await page.setViewportSize({width, height: Math.min(height, MAX_SCREENSHOT_HEIGHT)});
  }
}

/** Waits until the page has no requests in flight and no spinner, skeleton, or progress bar. */
async function waitForData(page: Page): Promise<void> {
  await page.waitForLoadState('networkidle');
  await expect(page.locator('[role="progressbar"], .MuiSkeleton-root')).toHaveCount(0, {timeout: 20_000});
  await page.waitForLoadState('networkidle');
  await page.evaluate(() => document.fonts.ready);
}

for (const c of PAGES) {
  const name = caseName(c);

  test(name, async ({page}, testInfo) => {
    const errors = watchForErrors(page);
    await page.clock.setFixedTime(frozenNow);
    await page.addInitScript(disableMotion);
    await page.goto(c.url);
    try {
      await waitForData(page);
    } catch {
      errors.push('page was still loading after 20s');
    }

    const notFound = await page.getByRole('heading', {name: 'Not Found', exact: true}).isVisible();
    const errorPage = await page.getByRole('heading', {name: 'An Unrecoverable Error Occurred'}).isVisible();
    if (notFound && !c.expectNotFound) errors.push('rendered the not-found page');
    if (errorPage) errors.push('rendered the error page');

    let screenshot: ScreenshotOutcome;
    if (side === 'base' && notFound && !c.expectNotFound) {
      // The route does not exist on the base yet. Writing no expectation makes the current
      // branch's render of it report as a new page.
      screenshot = 'skipped';
    } else {
      await fitViewportToContent(page);
      const before = testInfo.errors.length;
      await expect.soft(page).toHaveScreenshot(`${name}.png`, {fullPage: true});
      const failures = testInfo.errors.slice(before);
      if (failures.length === 0) screenshot = 'same';
      else if (failures.some((e) => /snapshot doesn't exist/.test(e.message ?? ''))) screenshot = 'new';
      else screenshot = 'changed';
    }

    writeRecord({name, url: c.url, errors, screenshot});
  });
}

// Fails the current branch's run when a <Route> in src/App.tsx has no case above, so a new page
// can't silently go unrendered. Skipped on the base, which is checked against the same table.
test('route coverage', async () => {
  test.skip(side !== 'head', 'checked against the current branch only');
  const appSource = fs.readFileSync(path.join(import.meta.dirname, '../../src/App.tsx'), 'utf8');
  const routes = [...appSource.matchAll(/<Route\s+path="([^"]+)"/g)].map((m) => m[1]);
  const covered = new Set(PAGES.map((c) => c.route));
  const errors = routes
    .filter((r) => !covered.has(r))
    .map((r) => `coverage: route "${r}" in src/App.tsx has no case in scripts/visual-diff/pages.spec.ts`);
  writeRecord({name: 'route-coverage', url: '', errors, screenshot: 'skipped'});
});
