export interface AccessConfig {
  ACCESS_TIME_LABELS: Record<string, string>;
  DEFAULT_ACCESS_TIME: string;
  NAME_VALIDATION_PATTERN: string;
  NAME_VALIDATION_ERROR: string;
  HIDE_ROLE_REQUESTS: boolean;
  HIDE_EXPIRING_ROLES: boolean;
  HIDE_OWNERSHIP_SELECTION: boolean;
  HIDE_GROUP_OWNER_BTNS: boolean;
  HIDE_GROUP_ROLE_MEMBER_BTNS: boolean;
  HIDE_GROUP_EDIT_BTN: boolean;
  HIDE_ROLE_EDIT_BTN: boolean;
  HIDE_ROLE_OWNER_BTNS: boolean;
  HIDE_ROLE_GROUPS_ADD_BTNS: boolean;
}

// use the globally-injected ACCESS_CONFIG from src/globals.d.ts, typed to AccessConfig interface
// see src/config/config.default.json for the default config
const accessConfig: AccessConfig = ACCESS_CONFIG as AccessConfig;

export default accessConfig;
