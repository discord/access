import assert from 'node:assert/strict';
import {describe, it} from 'node:test';

import {findSecretLikeText} from './secrets.mjs';

describe('findSecretLikeText', () => {
  it('flags common credential formats by name only', () => {
    const cases = [
      ['sk-ant-api03-abcdefghijklmnopqrstuvwx', 'anthropic-api-key'],
      ['sk-proj-abcdefghijklmnopqrstuvwxyz', 'openai-api-key'],
      ['ghp_abcdefghijklmnopqrstuvwxyz0123456789', 'github-token'],
      ['github_pat_11ABCDEFG0123456789_abcdef', 'github-token'],
      ['AKIAABCDEFGHIJKLMNOP', 'aws-access-key-id'],
      ['-----BEGIN RSA PRIVATE KEY-----', 'private-key'],
      ['xoxb-1234567890-abc', 'slack-token'],
      [`https://discord.com/api/webhooks/123456789012345678/${'a1B2-c3_D'.repeat(8)}`, 'discord-webhook-url'],
      [`https://canary.discordapp.com/api/v10/webhooks/12345678901234567/${'x'.repeat(68)}`, 'discord-webhook-url'],
      [`sntrys_${'eyJpYXQiOjE3MDAwMDAwMDAuMH0'.repeat(2)}_abc`, 'sentry-auth-token'],
      [`sntryu_${'0123456789abcdef'.repeat(4)}`, 'sentry-auth-token'],
    ];
    for (const [text, name] of cases) {
      assert.ok(findSecretLikeText(`value: ${text}`).includes(name), text);
    }
  });

  it('passes ordinary review prose', () => {
    const text = 'Use `can_manage_group(db, user, group)`; the task-runner and sk-learn skip the check.';
    assert.deepEqual(findSecretLikeText(text), []);
  });

  it('passes a webhook URL with no token and a bare Sentry prefix', () => {
    const text = 'POST to https://discord.com/api/webhooks/{id}/{token}; tokens start with sntrys_.';
    assert.deepEqual(findSecretLikeText(text), []);
  });

  it('catches a partial echo of a configured key', () => {
    const key = 'zz-custom-provider-key-9f8e7d6c5b4a';
    assert.deepEqual(findSecretLikeText(`leaked ${key.slice(5, 25)}!`, [key]), ['configured-api-key']);
    assert.deepEqual(findSecretLikeText('nothing here', [key]), []);
  });

  it('ignores configured secrets too short to match reliably', () => {
    assert.deepEqual(findSecretLikeText('short', ['short']), []);
  });
});
