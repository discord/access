#!/usr/bin/env node
/*
 * Renders every frontend page on the current branch and on its merge base with main, then
 * reports what changed. Run it with `npm run visual-diff`; `--help` lists the options.
 *
 * The current branch is built from the working tree as it stands, uncommitted changes
 * included. The base is checked out into a temporary git worktree, and its dependency install
 * and build are cached by commit, so only the first run against a given base pays for them.
 * Each side gets its own throwaway SQLite database, its own backend and static-file server on
 * free ports, and the same seed data, so a dev server already running is left alone. Nothing
 * talks to Okta.
 *
 * Exits 1 when the current branch shows a console, network, or render error the base doesn't,
 * and 0 otherwise; a screenshot diff alone does not fail, since it may be intended.
 */
import {spawn, spawnSync} from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import {parseArgs} from 'node:util';

const SCRIPT_DIR = import.meta.dirname;
const HEAD_ROOT = path.resolve(SCRIPT_DIR, '../..');
const OUT_DIR = path.join(HEAD_ROOT, '.visual-diff');
const CACHE_DIR = path.join(process.env.XDG_CACHE_HOME || path.join(os.homedir(), '.cache'), 'access-visual-diff');
const FIXTURES = JSON.parse(fs.readFileSync(path.join(SCRIPT_DIR, 'fixtures.json'), 'utf8'));
const KEEP_CACHED_BUILDS = 5;
const KEEP_CACHED_VENVS = 3;

const USAGE = `Usage: npm run visual-diff -- [options]

Renders every page on the current branch and on its merge base, and reports what changed.

Options:
  --base <ref>   Ref to diff against; the merge base of HEAD and this ref is rendered
                 (default: origin/main)
  --open         Serve the HTML report once the run finishes
  --help         Show this message`;

// ---------------------------------------------------------------------------------------------
// Process and cleanup bookkeeping
// ---------------------------------------------------------------------------------------------

const children = new Set();
const servers = new Set();
const cleanups = [];
let cleanupPromise = null;
let stopping = false;
let interrupted = false;

/**
 * Stops every child process and server, then runs the registered cleanups in reverse order.
 * Every caller gets the same promise, so none can exit while another is still cleaning up.
 */
function cleanup() {
  stopping = true;
  cleanupPromise ??= stopEverything();
  return cleanupPromise;
}

/** Does the work of `cleanup`; called once. */
async function stopEverything() {
  for (const child of children) killGroup(child, 'SIGTERM');
  for (const server of servers) server.close();
  await Promise.all([...children].map((child) => waitForExit(child, 3000)));
  for (const child of children) killGroup(child, 'SIGKILL');
  for (const fn of cleanups.reverse()) {
    try {
      fn();
    } catch (error) {
      console.error(`cleanup: ${error.message}`);
    }
  }
}

/** Sends a signal to a child's whole process group, if it is still running. */
function killGroup(child, signal) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  try {
    // Children are started in their own process group, so this also reaches their descendants
    // (npm's node, uvicorn's workers, Playwright's browsers).
    process.kill(-child.pid, signal);
  } catch {
    // Already gone.
  }
}

/** Resolves when a child exits or after `timeoutMs`, whichever comes first. */
function waitForExit(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, timeoutMs);
    child.once('exit', () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
  process.on(signal, async () => {
    interrupted = true;
    console.error(`\nvisual-diff: received ${signal}, cleaning up...`);
    await cleanup();
    process.exit(128 + os.constants.signals[signal]);
  });
}

/**
 * Starts a command in its own process group. Its output goes to `logFile` when one is given,
 * and to this terminal otherwise.
 */
function start(command, args, {cwd, env, logFile} = {}) {
  if (stopping) throw new Error('stopped');
  let stdio = ['ignore', 'inherit', 'inherit'];
  if (logFile) {
    const fd = fs.openSync(logFile, 'a');
    stdio = ['ignore', fd, fd];
  }
  const child = spawn(command, args, {cwd, env: env ?? process.env, stdio, detached: true});
  children.add(child);
  child.once('exit', () => children.delete(child));
  return child;
}

/** Runs a command to completion and returns its exit code; rejects on a non-zero exit unless `allowFailure`. */
function run(command, args, {allowFailure = false, ...options} = {}) {
  return new Promise((resolve, reject) => {
    const child = start(command, args, options);
    child.once('error', reject);
    child.once('exit', (code, signal) => {
      if (stopping) return reject(new Error('stopped'));
      if (code === 0 || allowFailure) return resolve(code ?? 1);
      const where = options.logFile ? `; see ${path.relative(process.cwd(), options.logFile)}` : '';
      reject(new Error(`${path.basename(command)} ${args.join(' ')} exited with ${code ?? signal}${where}`));
    });
  });
}

/** Runs a command and returns its trimmed stdout. */
function capture(command, args, {cwd} = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {cwd, stdio: ['ignore', 'pipe', 'pipe']});
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (d) => (stdout += d));
    child.stderr.on('data', (d) => (stderr += d));
    child.once('error', reject);
    child.once('exit', (code) =>
      code === 0 ? resolve(stdout.trim()) : reject(new Error(`${command} ${args.join(' ')}: ${stderr.trim()}`)),
    );
  });
}

// ---------------------------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------------------------

const startedAt = Date.now();
/** Prints a progress line prefixed with the seconds since the run started. */
function log(message) {
  const seconds = ((Date.now() - startedAt) / 1000).toFixed(0).padStart(4);
  console.log(`[${seconds}s] ${message}`);
}

/** Returns a TCP port that nothing is listening on right now. */
function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const {port} = server.address();
      server.close(() => resolve(port));
    });
  });
}

/** Returns a short content hash of the given files, relative to `root`. */
function hashFiles(root, files) {
  const hash = crypto.createHash('sha256');
  for (const file of files) hash.update(fs.readFileSync(path.join(root, file)));
  return hash.digest('hex').slice(0, 16);
}

/** Deletes all but the `keep` most recently used entries of a cache directory. */
function pruneCache(dir, keep) {
  if (!fs.existsSync(dir)) return;
  const entries = fs
    .readdirSync(dir)
    .map((name) => ({name, mtime: fs.statSync(path.join(dir, name)).mtimeMs}))
    .sort((a, b) => b.mtime - a.mtime);
  for (const {name} of entries.slice(keep)) fs.rmSync(path.join(dir, name), {recursive: true, force: true});
}

/** Marks a cache entry as used, so pruning keeps it. */
function touch(dir) {
  const now = new Date();
  fs.utimesSync(dir, now, now);
}

/**
 * The environment a side's frontend is built with. The keys the app reads at build time are
 * pinned so a developer's shell or .env can't make the two sides differ, and NODE_ENV is
 * development so React and MUI keep the warnings the spec collects.
 */
function buildEnv() {
  const env = Object.fromEntries(
    Object.entries(process.env).filter(
      ([key]) => !key.startsWith('VITE_') && !key.startsWith('SENTRY_') && key !== 'ACCESS_CONFIG_FILE',
    ),
  );
  return {
    ...env,
    NODE_ENV: 'development',
    APP_NAME: 'Access',
    REQUIRE_DESCRIPTIONS: 'false',
    REQUIRE_REQUEST_REASONS: 'false',
    VITE_API_SERVER_URL: '',
  };
}

/**
 * The environment a side's Python processes run with: a minimal one, so no OKTA_* or other
 * settings leak in from the developer's shell, and run from a scratch directory, so the
 * checkout's .env isn't read either.
 */
function pythonEnv(root, extra = {}) {
  return {
    PATH: process.env.PATH,
    HOME: process.env.HOME,
    TMPDIR: process.env.TMPDIR ?? os.tmpdir(),
    LANG: process.env.LANG ?? 'en_US.UTF-8',
    PYTHONPATH: root,
    PYTHONDONTWRITEBYTECODE: '1',
    ...extra,
  };
}

// ---------------------------------------------------------------------------------------------
// Preparing a side
// ---------------------------------------------------------------------------------------------

/**
 * Creates (or reuses) a virtualenv holding a side's locked runtime dependencies. The env is
 * keyed by the lockfile, so both sides share one when the branch doesn't change Python
 * dependencies, and dev-only plugins installed in the checkout's own .venv stay out of it.
 */
async function prepareVenv(root, logFile) {
  const key = hashFiles(root, ['uv.lock', 'pyproject.toml']);
  const venv = path.join(CACHE_DIR, 'venvs', key);
  if (!fs.existsSync(path.join(venv, '.visual-diff-complete'))) {
    log(`creating Python environment ${key}`);
    await run('uv', ['sync', '--frozen', '--no-dev', '--no-install-project', '--quiet'], {
      cwd: root,
      env: {...process.env, UV_PROJECT_ENVIRONMENT: venv},
      logFile,
    });
    fs.writeFileSync(path.join(venv, '.visual-diff-complete'), '');
  }
  touch(venv);
  return path.join(venv, 'bin', 'python');
}

/** Builds a side's frontend into `outDir` with that side's own Vite and config. */
async function buildFrontend(root, outDir, logFile) {
  await run(
    path.join(root, 'node_modules', '.bin', 'vite'),
    // Unminified so the component names in React's warnings match across the two builds.
    ['build', '--mode', 'development', '--minify', 'false', '--outDir', outDir, '--emptyOutDir', '--logLevel', 'warn'],
    {cwd: root, env: buildEnv(), logFile},
  );
}

/** Returns the base's built frontend, installing and building in the worktree on a cache miss. */
async function prepareBaseFrontend(worktree, sha, logFile) {
  const cached = path.join(CACHE_DIR, 'builds', sha);
  if (fs.existsSync(path.join(cached, 'index.html'))) {
    log('using cached base build');
    touch(cached);
    return cached;
  }
  log('installing base dependencies (first run against this base; cached afterwards)');
  // --ignore-scripts: husky's `prepare` would rewrite the core.hooksPath every worktree shares.
  await run('npm', ['ci', '--ignore-scripts', '--no-audit', '--no-fund'], {cwd: worktree, logFile});
  log('building base frontend');
  const staging = `${cached}.partial-${process.pid}`;
  cleanups.push(() => fs.rmSync(staging, {recursive: true, force: true}));
  await buildFrontend(worktree, staging, logFile);
  fs.rmSync(cached, {recursive: true, force: true});
  fs.renameSync(staging, cached);
  return cached;
}

/** Creates a side's schema from its own models and fills it with the shared seed data. */
async function seed(python, root, dbFile, anchor, scratch, logFile) {
  await run(python, [path.join(SCRIPT_DIR, 'seed.py'), dbFile, anchor], {
    cwd: scratch,
    env: pythonEnv(root),
    logFile,
  });
}

/** Starts a side's API on a free port and resolves with the port once it reports healthy. */
async function startBackend(python, root, dbFile, scratch, logFile) {
  const port = await freePort();
  const child = start(python, ['-m', 'uvicorn', 'api.asgi:app', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: scratch,
    env: pythonEnv(root, {
      ENV: 'development',
      DATABASE_URI: `sqlite:///${dbFile}`,
      CURRENT_OKTA_USER_EMAIL: FIXTURES.adminEmail,
      ENABLE_MCP: 'false',
    }),
    logFile,
  });
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) break;
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/healthz`);
      if (response.ok) return port;
    } catch {
      // Not listening yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`backend did not become healthy; see ${path.relative(process.cwd(), logFile)}`);
}

const CONTENT_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.map': 'application/json',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8',
  '.woff2': 'font/woff2',
};

/**
 * Serves a built frontend and proxies /api to a side's backend. Both sides use this one
 * server, so nothing about how the pages are served differs between them.
 */
function startStaticServer(buildDir, backendPort) {
  const root = path.resolve(buildDir);
  const server = http.createServer((req, res) => {
    if (req.url.startsWith('/api/')) {
      const upstream = http.request(
        {host: '127.0.0.1', port: backendPort, path: req.url, method: req.method, headers: req.headers},
        (upstreamRes) => {
          res.writeHead(upstreamRes.statusCode, upstreamRes.headers);
          upstreamRes.pipe(res);
        },
      );
      upstream.on('error', () => {
        res.writeHead(502);
        res.end();
      });
      req.pipe(upstream);
      return;
    }
    const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    let file = path.join(root, pathname);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      file = path.join(root, 'index.html');
    }
    res.writeHead(200, {'Content-Type': CONTENT_TYPES[path.extname(file)] ?? 'application/octet-stream'});
    fs.createReadStream(file).pipe(res);
  });
  servers.add(server);
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));
}

// ---------------------------------------------------------------------------------------------
// Comparing the two sides
// ---------------------------------------------------------------------------------------------

/** Loads the per-page records one Playwright run wrote, keyed by page name. */
function readRecords(dir) {
  if (!fs.existsSync(dir)) return new Map();
  return new Map(
    fs
      .readdirSync(dir)
      .filter((f) => f.endsWith('.json'))
      .map((f) => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8')))
      .map((record) => [record.name, record]),
  );
}

/** Strips what legitimately differs between the two sides (ports, asset hashes, line numbers). */
function normalizeError(message) {
  return message
    .replace(/https?:\/\/127\.0\.0\.1:\d+/g, '<origin>')
    .replace(/-[A-Za-z0-9_-]{8}\.js/g, '.js')
    .replace(/:\d+:\d+/g, '')
    .replace(/[ \t]+/g, ' ')
    .trim();
}

/** Sorts the branch's pages into changed and new, and its errors into new and fixed. */
function summarize(baseRecords, headRecords, expectedNames) {
  const changed = [];
  const added = [];
  const newErrors = [];
  const fixedErrors = [];
  for (const name of expectedNames) {
    const head = headRecords.get(name);
    const base = baseRecords.get(name);
    if (!head) {
      newErrors.push({name, error: 'the spec did not finish this page; see the report'});
      continue;
    }
    if (head.screenshot === 'changed') changed.push(name);
    if (head.screenshot === 'new') added.push(name);
    const baseErrors = new Set((base?.errors ?? []).map(normalizeError));
    const headErrors = new Set(head.errors.map(normalizeError));
    for (const error of headErrors) if (!baseErrors.has(error)) newErrors.push({name, error});
    // A new page's base record only says the route didn't exist yet; nothing there was fixed.
    if (head.screenshot === 'new') continue;
    for (const error of baseErrors) if (!headErrors.has(error)) fixedErrors.push({name, error});
  }
  return {changed, added, newErrors, fixedErrors};
}

// ---------------------------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------------------------

async function main() {
  const {values} = parseArgs({
    options: {
      base: {type: 'string', default: 'origin/main'},
      open: {type: 'boolean', default: false},
      help: {type: 'boolean', default: false},
    },
  });
  if (values.help) {
    console.log(USAGE);
    return 0;
  }

  const playwright = path.join(HEAD_ROOT, 'node_modules', '.bin', 'playwright');
  if (!fs.existsSync(playwright)) {
    throw new Error('@playwright/test is not installed; run `npm ci --ignore-scripts` first');
  }
  await capture('uv', ['--version']).catch(() => {
    throw new Error('uv is not on PATH; see the README for setup');
  });

  const sha = await capture('git', ['merge-base', 'HEAD', values.base], {cwd: HEAD_ROOT});
  const subject = await capture('git', ['log', '-1', '--format=%h %s', sha], {cwd: HEAD_ROOT});
  log(`base: ${subject} (merge base of HEAD and ${values.base})`);

  fs.rmSync(OUT_DIR, {recursive: true, force: true});
  const logs = path.join(OUT_DIR, 'logs');
  fs.mkdirSync(logs, {recursive: true});
  fs.mkdirSync(path.join(CACHE_DIR, 'builds'), {recursive: true});
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'access-visual-diff-'));
  cleanups.push(() => fs.rmSync(scratch, {recursive: true, force: true}));

  const worktree = path.join(scratch, 'base');
  // Registered first so an interrupted `worktree add` is undone too. Synchronous, because it
  // runs after every child has been stopped and no new one may start.
  cleanups.push(() => {
    spawnSync('git', ['worktree', 'remove', '--force', worktree], {cwd: HEAD_ROOT, stdio: 'ignore'});
    spawnSync('git', ['worktree', 'prune'], {cwd: HEAD_ROOT, stdio: 'ignore'});
  });
  await run('git', ['worktree', 'add', '--quiet', '--detach', worktree, sha], {cwd: HEAD_ROOT});

  // Every timestamp the seed writes is an offset from this, and the browser clock is frozen
  // relative to it. Computed once so both sides agree even across midnight.
  const today = new Date();
  today.setUTCHours(0, 0, 0, 0);
  const anchor = today.toISOString();

  const browsers = run(playwright, ['install', 'chromium'], {
    cwd: HEAD_ROOT,
    logFile: path.join(logs, 'playwright-install.log'),
  });
  // Sequential: both sides share one venv when the lockfile is unchanged.
  const basePython = await prepareVenv(worktree, path.join(logs, 'base-python.log'));
  const headPython = await prepareVenv(HEAD_ROOT, path.join(logs, 'head-python.log'));

  const headBuild = path.join(scratch, 'head-build');
  const [baseBuild] = await Promise.all([
    prepareBaseFrontend(worktree, sha, path.join(logs, 'base-frontend.log')),
    (async () => {
      log('building current branch frontend');
      await buildFrontend(HEAD_ROOT, headBuild, path.join(logs, 'head-frontend.log'));
    })(),
    browsers,
  ]);
  pruneCache(path.join(CACHE_DIR, 'builds'), KEEP_CACHED_BUILDS);
  pruneCache(path.join(CACHE_DIR, 'venvs'), KEEP_CACHED_VENVS);

  log('seeding and starting both backends');
  const sides = {
    base: {root: worktree, python: basePython, build: baseBuild},
    head: {root: HEAD_ROOT, python: headPython, build: headBuild},
  };
  await Promise.all(
    Object.entries(sides).map(async ([name, side]) => {
      const db = path.join(scratch, `${name}.db`);
      const backendLog = path.join(logs, `${name}-backend.log`);
      await seed(side.python, side.root, db, anchor, scratch, backendLog);
      const backendPort = await startBackend(side.python, side.root, db, scratch, backendLog);
      side.url = `http://127.0.0.1:${await startStaticServer(side.build, backendPort)}`;
    }),
  );

  const config = path.join(SCRIPT_DIR, 'playwright.config.ts');
  const playwrightEnv = (name) => ({
    ...process.env,
    VISUAL_DIFF_SIDE: name,
    VISUAL_DIFF_BASE_URL: sides[name].url,
    VISUAL_DIFF_OUT_DIR: OUT_DIR,
    VISUAL_DIFF_RESULTS_DIR: path.join(OUT_DIR, 'results', name),
    VISUAL_DIFF_ANCHOR: anchor,
  });
  log('rendering base');
  await run(playwright, ['test', '--config', config, '--update-snapshots=all'], {
    cwd: HEAD_ROOT,
    env: playwrightEnv('base'),
    logFile: path.join(logs, 'base-playwright.log'),
    allowFailure: true,
  });
  log('rendering current branch');
  await run(playwright, ['test', '--config', config], {
    cwd: HEAD_ROOT,
    env: playwrightEnv('head'),
    logFile: path.join(logs, 'head-playwright.log'),
    allowFailure: true,
  });

  const baseRecords = readRecords(path.join(OUT_DIR, 'results', 'base'));
  const headRecords = readRecords(path.join(OUT_DIR, 'results', 'head'));
  const expectedNames = [...new Set([...baseRecords.keys(), ...headRecords.keys()])].sort();
  const summary = summarize(baseRecords, headRecords, expectedNames);
  fs.writeFileSync(path.join(OUT_DIR, 'summary.json'), JSON.stringify({base: sha, ...summary}, null, 2));

  const report = path.relative(process.cwd(), path.join(OUT_DIR, 'report'));
  console.log('');
  console.log(`Visual diff against ${subject}, ${expectedNames.length} pages, ${log.elapsed()}:`);
  const list = (title, items) => {
    console.log(`  ${title}:${items.length === 0 ? ' none' : ''}`);
    for (const item of items) console.log(`    - ${item}`);
  };
  list('Changed pages', summary.changed);
  list('New pages', summary.added);
  list(
    'Errors only on this branch',
    // React's warnings carry a component stack; the first line is enough to find it in summary.json.
    summary.newErrors.map(({name, error}) => `${name}: ${error.split('\n')[0]}`),
  );
  if (summary.fixedErrors.length > 0) {
    console.log(`  Errors on the base that this branch no longer has: ${summary.fixedErrors.length}`);
  }
  console.log('');
  console.log(`Report (actual, expected, and diff images): ${report}/index.html`);
  console.log(`  open it with: npx playwright show-report ${report}`);
  console.log(`Logs: ${path.relative(process.cwd(), logs)}`);

  await cleanup();
  if (values.open && !interrupted) {
    // In this process's own group, so Ctrl-C reaches it directly once the report is up.
    await new Promise((resolve) =>
      spawn(playwright, ['show-report', report], {cwd: HEAD_ROOT, stdio: 'inherit'}).once('exit', resolve),
    );
  }
  return summary.newErrors.length > 0 ? 1 : 0;
}

log.elapsed = () => `${Math.round((Date.now() - startedAt) / 1000)}s`;

// After a signal, the handler owns the exit; anything main() does as its children die is moot.
main()
  .then(async (code) => {
    if (interrupted) return;
    await cleanup();
    process.exit(code);
  })
  .catch(async (error) => {
    if (interrupted) return;
    console.error(`visual-diff: ${error.message}`);
    await cleanup();
    process.exit(2);
  });
