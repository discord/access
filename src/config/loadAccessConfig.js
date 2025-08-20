/*
 *  JS used in `vite.config.ts` to load `ACCESS_CONFIG` as a global variable in the frontend
 *  If you want to use `AccessConfig` in the frontend, use `accessConfig` in `accessConfig.ts`
 * */

import fs from 'fs';
import path from 'path';

const ACCESS_TIME_LABELS = 'ACCESS_TIME_LABELS';
const DEFAULT_ACCESS_TIME = 'DEFAULT_ACCESS_TIME';
const FRONTEND = 'FRONTEND';
const NAME_VALIDATION_PATTERN = 'NAME_VALIDATION_PATTERN';
const NAME_VALIDATION_ERROR = 'NAME_VALIDATION_ERROR';

class UndefinedConfigError extends Error {
  constructor(key, obj) {
    const message = `'${key}' is not a defined config value in AccessConfig`;
    super(message);
  }
}

class AccessConfigValidationError extends Error {
  constructor(message) {
    super(message);
  }
}

class ConfigFileNotFoundError extends Error {
  constructor(filePath) {
    const message = `Config override file not found: ${filePath}`;
    super(message);
  }
}

function getConfig(obj, key) {
  if (key in obj) {
    return obj[key];
  } else {
    throw new UndefinedConfigError(String(key), obj);
  }
}

function validateConfig(accessConfig) {
  if (ACCESS_TIME_LABELS in accessConfig && typeof accessConfig[ACCESS_TIME_LABELS] !== 'object') {
    throw new AccessConfigValidationError(`${ACCESS_TIME_LABELS} must be a dictionary`);
  }

  if (DEFAULT_ACCESS_TIME in accessConfig) {
    const defaultAccessTime = parseInt(getConfig(accessConfig, DEFAULT_ACCESS_TIME), 10);
    if (isNaN(defaultAccessTime) || !getConfig(accessConfig, ACCESS_TIME_LABELS).hasOwnProperty(defaultAccessTime)) {
      throw new AccessConfigValidationError(`${DEFAULT_ACCESS_TIME} must be a valid key in ${ACCESS_TIME_LABELS}`);
    }
  }
}

function validate_override_config(overrideConfig) {
  if (NAME_VALIDATION_PATTERN in overrideConfig && !(NAME_VALIDATION_ERROR in overrideConfig)) {
    throw new AccessConfigValidationError(
      `If ${NAME_VALIDATION_PATTERN} is present, ${NAME_VALIDATION_ERROR} must also be overridden.`,
    );
  }
}

function loadOverrideConfig(accessConfig) {
  const envConfigPath = process.env.ACCESS_CONFIG_FILE
    ? path.resolve(__dirname, '../../config', process.env.ACCESS_CONFIG_FILE)
    : null;
  if (envConfigPath) {
    if (fs.existsSync(envConfigPath)) {
      const envConfig = JSON.parse(fs.readFileSync(envConfigPath, 'utf8'));
      if (FRONTEND in envConfig) {
        const frontendConfig = getConfig(envConfig, FRONTEND);
        validate_override_config(frontendConfig);
        Object.assign(accessConfig, frontendConfig);
      }
    } else {
      throw new ConfigFileNotFoundError(envConfigPath);
    }
  }
  return accessConfig;
}

function loadDefaultConfig() {
  const defaultConfigPath = path.resolve(__dirname, '../../config/config.default.json');
  const defaultConfig = JSON.parse(fs.readFileSync(defaultConfigPath, 'utf8'));

  return getConfig(defaultConfig, FRONTEND);
}

export function loadAccessConfig() {
  try {
    let accessConfig = loadDefaultConfig();
    accessConfig = loadOverrideConfig(accessConfig);

    validateConfig(accessConfig);

    return JSON.stringify(accessConfig);
  } catch (error) {
    console.error('Error loading access configuration:', error);
    throw error;
  }
}
