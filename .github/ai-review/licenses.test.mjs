import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {describe, it} from 'node:test';

// .github/dependency-review-config.yml exempts these proprietary packages from the license check
// because only the AI review gate's agent image installs them, and that image is built and
// discarded inside the review job. The exemption applies repo-wide, so this test keeps them out of
// every other manifest.
const PROPRIETARY = /"(?:node_modules\/)?@anthropic-ai\/claude-code(?:-[a-z0-9-]+)?"/u;
const AGENT_DIR = '.github/ai-review/agent/';

const root = execFileSync('git', ['rev-parse', '--show-toplevel'], {encoding: 'utf8'}).trim();

/** Tracked `package.json` and `package-lock.json` files, as repo-relative paths. */
function trackedManifests() {
  return execFileSync('git', ['ls-files', '--', '*package.json', '*package-lock.json'], {cwd: root, encoding: 'utf8'})
    .split('\n')
    .filter((file) => /(?:^|\/)package(?:-lock)?\.json$/u.test(file));
}

describe('proprietary dependencies', () => {
  it('appear only in the agent image manifests', () => {
    const manifests = trackedManifests();
    assert.ok(manifests.includes(`${AGENT_DIR}package.json`), 'the agent manifest is tracked');
    const offenders = manifests.filter(
      (file) => !file.startsWith(AGENT_DIR) && PROPRIETARY.test(fs.readFileSync(path.join(root, file), 'utf8')),
    );
    assert.deepEqual(offenders, []);
  });

  it('match the package and its platform builds but not other Anthropic packages', () => {
    assert.ok(PROPRIETARY.test('"@anthropic-ai/claude-code": "2.1.318"'));
    assert.ok(PROPRIETARY.test('"node_modules/@anthropic-ai/claude-code-linux-x64-musl": {'));
    assert.ok(!PROPRIETARY.test('"@anthropic-ai/sdk": "1.0.0"'));
  });
});
