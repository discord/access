export interface AccessConfig {
  ACCESS_TIME_LABELS: Record<string, string>;
  DEFAULT_ACCESS_TIME: string;
  NAME_VALIDATION_PATTERN: string;
  NAME_VALIDATION_ERROR: string;
  APP_GROUP_NAME_PREFIX: string;
  APP_NAME_GROUP_NAME_SEPARATOR: string;
  ROLE_GROUP_NAME_PREFIX: string;
}

// use the globally-injected ACCESS_CONFIG from src/globals.d.ts, typed to AccessConfig interface
// see src/config/config.default.json for the default config
const accessConfig: AccessConfig = ACCESS_CONFIG as AccessConfig;

export default accessConfig;

export const appName = APP_NAME;
export const requireDescriptions = REQUIRE_DESCRIPTIONS;
