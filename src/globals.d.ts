declare const ACCESS_CONFIG: any;
declare const APP_NAME: string;
declare const REQUIRE_DESCRIPTIONS: boolean;

interface ImportMetaEnv {
  readonly VITE_API_SERVER_URL: string;
  readonly VITE_SENTRY_RELEASE: string;
  readonly MODE: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
