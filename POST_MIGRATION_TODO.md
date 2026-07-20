# Post-Migration TODO

Follow-up work after the [Flask + Marshmallow → FastAPI + Pydantic v2 migration](https://github.com/discord/access/pull/425). Each item is intentionally **deferred** — the initial migration prioritized wire compatibility over idiomatic FastAPI, so several "make it nicer" changes were postponed to keep that diff focused and reviewable.

Items grouped roughly by surface area; ordering within each group is rough priority. Most items can be done independently of each other except where called out.

---

## Test Ergonomics

### 16. Replace `factory_boy` with Pydantic-based builders

Either:
- Keep `factory_boy` but decouple it from the legacy SQLAlchemy session
  pattern, or
- Replace with [`polyfactory`](https://polyfactory.litestar.dev/) which
  generates fixtures from Pydantic models — keeps test data and request
  schemas in sync automatically.

---

## Security follow-ups (out of scope for the migration PR)

### 20. Nonce-based CSP

Drop `'unsafe-inline'` from `script-src` and `style-src` in
`api/middleware.py`. Generate a per-response nonce in
`SecurityHeadersMiddleware` and thread it through `build/index.html`
+ the React build pipeline so every inline `<script>` / `<style>`
carries the nonce. Touches the frontend; not a same-PR fix.
