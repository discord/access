# Visual diff

`npm run visual-diff` renders every page in `src/App.tsx` on the current branch and on its merge
base with `origin/main`, then reports what changed. tsc and vitest don't render pages, so this is
how to check a change that could alter what users see without clicking through every page by
hand. Use it for:

- frontend dependency upgrades (MUI, MUI X, React, React Router, Emotion), and
- layout, theme, or shared-component changes that touch many pages at once.

It is opt-in and local, not a CI check. Nothing it produces is checked in: both sides render on
your machine in the same run, so there are no stored baselines and operating-system font
differences don't matter.

```bash
npm run visual-diff                        # diff against the merge base with origin/main
npm run visual-diff -- --base upstream/main  # diff against a different ref's merge base
npm run visual-diff -- --open              # serve the HTML report when done
```

It prints the pages whose screenshots changed, the pages that are new on the branch, and the
errors that appear only on the branch: uncaught exceptions, `console.error` output (React and MUI
warnings included; the frontend is built in development mode so they aren't stripped), failed
`/api/*` requests, and pages that render the not-found or error page. It exits 1 only when the
branch has new errors. A screenshot diff alone exits 0, since it may be the point of the change.
The Playwright HTML report under `.visual-diff/report/` shows the expected, actual, and diff
images for each changed page; `.visual-diff/summary.json` has the full error text and
`.visual-diff/logs/` has every build, backend, and Playwright log.

## How it works

- **The base runs in a temporary `git worktree`** of `git merge-base HEAD <base>`, installed with
  `npm ci --ignore-scripts` so husky can't rewrite the shared `core.hooksPath`. The current branch
  is built from the working tree as it stands, uncommitted changes included. Both builds go to
  temporary directories, so the checkout's `build/` is untouched.
- **Each side gets its own SQLite database, backend, and static server on free ports.** The
  backends run in a virtualenv built from that side's `uv.lock` without dev dependencies, from a
  scratch directory, with a minimal environment, so neither the checkout's `.env` nor plugins
  installed in its `.venv` leak in. A dev server already running on 3000 or 6060 is left alone.
- **Both sides are seeded by `seed.py` from the current branch** with fixed ids
  (`fixtures.json`) and timestamps offset from midnight UTC of the run. The browser clock is frozen
  relative to the same point, so relative dates ("in 5 days") match. Nothing calls Okta.
- **One spec, `pages.spec.ts` from the current branch, renders both sides.** The base run writes
  the screenshots and the branch run compares against them. A route the branch adds renders the
  not-found page on the base, so no expectation is written and the page reports as new rather than
  changed.
- **The base's install and build are cached by commit**, and virtualenvs by lockfile, under
  `${XDG_CACHE_HOME:-~/.cache}/access-visual-diff`. The first run against a new merge base takes
  about 35 seconds on a laptop with warm npm and uv caches; later runs take about 25. The first
  run on a machine also downloads Playwright's Chromium.
- Ctrl-C stops every process it started and removes the worktree and temporary files.

## Screenshot noise

Up to 100 differing pixels per page count as noise: two launches of the same headless Chromium
still antialias a few glyphs a shade differently, by up to about 50 pixels on a page. A change to
spacing or layout moves far more than that, but a color change confined to a single word can land
near the limit (a grey "None" turning black moved about 150), so look closely at pages that
change only slightly.

## Keeping it current

- **New or renamed route:** add a case to `PAGES` in `pages.spec.ts`. If the page needs data the
  seed doesn't have, add the rows to `seed.py` and their ids to `fixtures.json`. A run fails
  while a `<Route>` in `src/App.tsx` has no case.
- **Model change to a column the seed sets:** update `seed.py`. Both sides are seeded by the
  branch's copy, so it can only use columns that exist on both the branch and its merge base;
  a removed or renamed column breaks seeding of whichever side lacks it.
- **New loading indicator:** the spec waits for `[role="progressbar"]` and `.MuiSkeleton-root`
  to disappear. A loader that matches neither lets screenshots capture a half-loaded page; widen
  the selector in `waitForData`.
