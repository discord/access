// @vitest-environment node
import fs from 'fs';
import os from 'os';
import path from 'path';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';

import {loadAccessConfig} from './loadAccessConfig.js';

let tempDir;

function loadWithOverride(frontend) {
  const file = path.join(tempDir, 'config.test.json');
  fs.writeFileSync(file, JSON.stringify({FRONTEND: frontend}));
  // The loader resolves the name against the project's config directory, which an
  // absolute path replaces.
  vi.stubEnv('ACCESS_CONFIG_FILE', file);
  return JSON.parse(loadAccessConfig());
}

beforeEach(() => {
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'access-config-'));
  vi.spyOn(globalThis.console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  fs.rmSync(tempDir, {recursive: true, force: true});
});

describe('DEFAULT_OWNERSHIP_ACCESS_TIME', () => {
  it('follows DEFAULT_ACCESS_TIME when an operator sets only that', () => {
    const config = loadWithOverride({DEFAULT_ACCESS_TIME: '432000'});
    expect(config.DEFAULT_OWNERSHIP_ACCESS_TIME).toBe('432000');
  });

  it('accepts indefinite', () => {
    const config = loadWithOverride({DEFAULT_OWNERSHIP_ACCESS_TIME: 'indefinite'});
    expect(config.DEFAULT_OWNERSHIP_ACCESS_TIME).toBe('indefinite');
    expect(config.DEFAULT_ACCESS_TIME).toBe('1209600');
  });

  it('accepts a number of seconds that has a label', () => {
    const config = loadWithOverride({DEFAULT_OWNERSHIP_ACCESS_TIME: '7776000'});
    expect(config.DEFAULT_OWNERSHIP_ACCESS_TIME).toBe('7776000');
  });

  it.each(['custom', '86400', 'forever'])('rejects %s', (value) => {
    expect(() => loadWithOverride({DEFAULT_OWNERSHIP_ACCESS_TIME: value})).toThrow(
      'DEFAULT_OWNERSHIP_ACCESS_TIME must be',
    );
  });
});
