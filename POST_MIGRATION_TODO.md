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
