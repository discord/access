declare const ACCESS_CONFIG: any;
declare const APP_NAME: string;
declare const REQUIRE_DESCRIPTIONS: boolean;

interface ImportMetaEnv {
  readonly VITE_API_SERVER_URL: string;
  // Undefined unless the build sets `SENTRY_RELEASE`; see the `define` in vite.config.ts.
  readonly VITE_SENTRY_RELEASE: string | undefined;
  readonly MODE: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
