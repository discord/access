# Access — Frontend development

Companion to `.claude/CLAUDE.md`. Read this when working on the React frontend (anything under
`src/`). See the main file for backend structure, security goals, and cross-cutting rules.

## Frontend

Frontend code — all pages, dialogs, and components in `src/pages/` and `src/components/` —
is written by hand or with Claude, not generated from a schema. **The entire `src/api/`
directory is generated from the backend OpenAPI spec and must not be edited manually**
(`apiComponents.ts`, `apiSchemas.ts`, `apiContext.ts`, `apiUtils.ts`, and the lightly
hand-customized `apiFetcher.ts` — which the generator preserves: RFC 9457 error flattening,
`VITE_API_SERVER_URL`, and `queryKeyFn`).

Regenerate after backend schema changes with `npm run codegen`. It reads the spec from the
running dev backend (`http://localhost:6060/api/openapi.json`), so the backend must be up —
or point it at a dumped spec file via `OPENAPI_SPEC_FILE`. A CI drift check
(`.github/workflows/openapi-client-drift.yml`) fails any PR whose `src/api/` is out of sync
with the spec, so commit the regenerated client alongside the backend change.

The data-fetching layer is React Query **v5** (`@tanstack/react-query`); mind the v5 renames
(`cacheTime` → `gcTime`, object-form hook args) when copying older call-site patterns.

## Layout and design principles

Follow the patterns established in existing pages before introducing new structure.

**Colors:** Use theme colors only. Don't hardcode hex values or add one-off colors outside the
theme. If a new color is genuinely needed, add it to the theme rather than inline.

**Page structure:** All content must live on a card or card-like surface — never place text or
interactive elements directly on the background layer of a page. The top bar and navigation
bar are the only exceptions.

**Early validation:** Surface input errors as soon as they're detectable — don't wait for the
user to submit. If a value would be rejected by the API (e.g. a name using the reserved
`App-` prefix for an OktaGroup), show the error inline on the field so the user can fix it
immediately. The API must still enforce the same constraint for requests that bypass the UI.
