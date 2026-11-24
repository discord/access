export interface AccessConfig {
  ACCESS_TIME_LABELS: Record<string, string>;
  DEFAULT_ACCESS_TIME: string;
  DEFAULT_OWNERSHIP_ACCESS_TIME: string;
  NAME_VALIDATION_PATTERN: string;
  NAME_VALIDATION_ERROR: string;
  IDP_NAME: string;
  IDP_USER_URL_TEMPLATE: string;
  IDP_GROUP_URL_TEMPLATE: string;
}

// use the globally-injected ACCESS_CONFIG from src/globals.d.ts, typed to AccessConfig interface
// see src/config/config.default.json for the default config
const accessConfig: AccessConfig = ACCESS_CONFIG as AccessConfig;

export default accessConfig;

/**
 * The access-time label a request form starts on.
 *
 * Ownership and membership have separate defaults so an operator can start ownership
 * requests on a longer duration than membership requests. A tag time limit still narrows
 * the choice once the form knows which group the request is for.
 *
 * @param owner Whether the request is for ownership rather than membership.
 * @returns A key of `ACCESS_TIME_LABELS`: `DEFAULT_OWNERSHIP_ACCESS_TIME` for ownership,
 *   `DEFAULT_ACCESS_TIME` for membership.
 */
export function defaultRequestAccessTime(owner: boolean): string {
  return owner ? accessConfig.DEFAULT_OWNERSHIP_ACCESS_TIME : accessConfig.DEFAULT_ACCESS_TIME;
}

export const appName = APP_NAME;
export const requireDescriptions = REQUIRE_DESCRIPTIONS;
export const requireRequestReasons = REQUIRE_REQUEST_REASONS;
