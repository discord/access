// set a default set of values for UNTIL_ID_TO_LABELS that can be overridden in the environment
const UNTIL_ID_TO_LABELS_DEFAULT: Record<string, string> = {
  '43200': '12 Hours',
  '432000': '5 Days',
  '1209600': 'Two Weeks',
  '2592000': '30 Days',
  '7776000': '90 Days',
  indefinite: 'Indefinite',
  custom: 'Custom',
} as const;

// Uses process.env.REACT_APP_UNTIL_ID_TO_LABELS_OVERRIDE if defined, otherwise uses default
const UNTIL_ID_TO_LABELS_CONFIG: Record<string, string> = process.env.REACT_APP_UNTIL_ID_TO_LABELS_OVERRIDE
  ? JSON.parse(process.env.REACT_APP_UNTIL_ID_TO_LABELS_OVERRIDE)
  : UNTIL_ID_TO_LABELS_DEFAULT;

export {UNTIL_ID_TO_LABELS_CONFIG};

// set a default set of values for UNTIL_JUST_NUMERIC_ID_TO_LABELS that can be overridden in the environment
const UNTIL_JUST_NUMERIC_ID_TO_LABELS_DEFAULT: Record<string, string> = {
  '43200': '12 Hours',
  '432000': '5 Days',
  '1209600': 'Two Weeks',
  '2592000': '30 Days',
  '7776000': '90 Days',
} as const;

// Uses process.env.REACT_APP_UNTIL_JUST_NUMERIC_ID_TO_LABELS_OVERRIDE if defined, otherwise uses default
const UNTIL_JUST_NUMERIC_ID_TO_LABELS_CONFIG: Record<string, string> = process.env.REACT_APP_UNTIL_JUST_NUMERIC_ID_TO_LABELS_OVERRIDE
  ? JSON.parse(process.env.REACT_APP_UNTIL_JUST_NUMERIC_ID_TO_LABELS_OVERRIDE)
  : UNTIL_JUST_NUMERIC_ID_TO_LABELS_DEFAULT;

export {UNTIL_JUST_NUMERIC_ID_TO_LABELS_CONFIG};

// Set Default value for React form state that can be overriden
const DEFAULT_ACCESS_TIME_DEFAULT = '1209600';

// Parse and export DEFAULT_ACCESS_TIME_CONFIG
export const DEFAULT_ACCESS_TIME_CONFIG: string = process.env.REACT_APP_DEFAULT_ACCESS_TIME_OVERRIDE
  ? process.env.REACT_APP_DEFAULT_ACCESS_TIME_OVERRIDE
  : DEFAULT_ACCESS_TIME_DEFAULT;
