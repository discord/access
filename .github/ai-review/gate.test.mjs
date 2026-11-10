import assert from 'node:assert/strict';
import {describe, it} from 'node:test';

import {TRIGGER_LABEL, decide, hasTriageOrHigher} from './gate.mjs';

const HEAD = 'a'.repeat(40);
const BASE = 'b'.repeat(40);

function event(overrides = {}, pullOverrides = {}) {
  return {
    action: 'opened',
    sender: {login: 'maintainer', type: 'User'},
    ...overrides,
    pull_request: {
      state: 'open',
      draft: false,
      user: {login: 'author', type: 'User'},
      author_association: 'CONTRIBUTOR',
      head: {sha: HEAD},
      base: {sha: BASE},
      ...pullOverrides,
    },
  };
}

function trusting(...logins) {
  const asked = [];
  const isTrusted = async (login) => {
    asked.push(login);
    return logins.includes(login);
  };
  return {isTrusted, asked};
}

async function run(payload, {hasKey = true, trusted = []} = {}) {
  const {isTrusted, asked} = trusting(...trusted);
  const decision = await decide({eventName: 'pull_request_target', event: payload, hasKey, isTrusted});
  return {decision, asked};
}

describe('decide', () => {
  it('is a silent no-op when no credential is configured', async () => {
    const {decision, asked} = await run(event(), {hasKey: false, trusted: ['author']});
    assert.equal(decision.run, false);
    assert.equal(decision.check, 'disabled');
    assert.match(decision.reason, /no model credential/u);
    assert.deepEqual(asked, []);
  });

  it('reviews a trusted author automatically, whatever author_association says', async () => {
    for (const action of ['opened', 'reopened', 'synchronize', 'ready_for_review']) {
      const {decision} = await run(event({action}), {trusted: ['author']});
      assert.equal(decision.run, true, action);
      assert.equal(decision.check, 'review', action);
      assert.equal(decision.headSha, HEAD);
      assert.equal(decision.baseSha, BASE);
      assert.equal(decision.labeled, false);
    }
  });

  it('does not trust author_association on its own', async () => {
    const {decision} = await run(event({}, {author_association: 'MEMBER'}));
    assert.equal(decision.run, false);
    assert.equal(decision.check, 'awaiting-label');
    assert.match(decision.reason, /lacks triage access/u);
  });

  it('passes drafts, closed pull requests, and bots without reviewing', async () => {
    for (const pull of [{draft: true}, {state: 'closed'}]) {
      const {decision} = await run(event({}, pull), {trusted: ['author']});
      assert.equal(decision.run, false);
      assert.equal(decision.check, 'pass');
    }
    for (const user of [
      {login: 'dependabot[bot]', type: 'Bot'},
      {login: 'renovate[bot]', type: 'User'},
    ]) {
      const {decision, asked} = await run(event({}, {user}), {trusted: [user.login]});
      assert.equal(decision.run, false, user.login);
      assert.equal(decision.check, 'pass', user.login);
      assert.deepEqual(asked, []);
    }
  });

  it('reviews the head recorded in a labeled event when the sender holds triage access', async () => {
    const {decision, asked} = await run(event({action: 'labeled', label: {name: TRIGGER_LABEL}}), {
      trusted: ['maintainer'],
    });
    assert.equal(decision.run, true);
    assert.equal(decision.check, 'review');
    assert.equal(decision.labeled, true);
    assert.equal(decision.headSha, HEAD);
    assert.deepEqual(asked, ['maintainer']);
  });

  it('re-checks the label sender rather than trusting that they could label', async () => {
    const {decision} = await run(event({action: 'labeled', label: {name: TRIGGER_LABEL}}), {trusted: ['author']});
    assert.equal(decision.run, false);
    assert.equal(decision.check, 'ignore');
    assert.equal(decision.labeled, true);
  });

  it('removes the label from a draft without reviewing it', async () => {
    const {decision} = await run(event({action: 'labeled', label: {name: TRIGGER_LABEL}}, {draft: true}), {
      trusted: ['maintainer'],
    });
    assert.equal(decision.run, false);
    assert.equal(decision.check, 'pass');
    assert.equal(decision.labeled, true);
  });

  it('ignores bot senders and other labels', async () => {
    const botLabel = event({action: 'labeled', label: {name: TRIGGER_LABEL}, sender: {login: 'x[bot]', type: 'Bot'}});
    const bot = (await run(botLabel, {trusted: ['x[bot]']})).decision;
    assert.equal(bot.run, false);
    assert.equal(bot.check, 'ignore');
    assert.equal(bot.labeled, true);
    const other = await run(event({action: 'labeled', label: {name: 'risk:auth'}}, {draft: true}), {
      trusted: ['maintainer'],
    });
    assert.equal(other.decision.run, false);
    assert.equal(other.decision.check, 'ignore');
    assert.equal(other.decision.labeled, false);
    assert.deepEqual(other.asked, []);
  });

  it('ignores other events and actions', async () => {
    const {isTrusted} = trusting('author');
    assert.equal((await decide({eventName: 'pull_request', event: event(), hasKey: true, isTrusted})).run, false);
    const edited = (await run(event({action: 'edited'}), {trusted: ['author']})).decision;
    assert.equal(edited.run, false);
    assert.equal(edited.check, 'ignore');
  });
});

describe('hasTriageOrHigher', () => {
  it('accepts triage and above, including custom roles with a trusted base', () => {
    assert.equal(hasTriageOrHigher({role_name: 'triage', user: {permissions: {triage: true}}}), true);
    assert.equal(hasTriageOrHigher({role_name: 'admin'}), true);
    assert.equal(hasTriageOrHigher({role_name: 'release-manager', user: {permissions: {push: true}}}), true);
  });

  it('rejects read access and unknown users', () => {
    assert.equal(hasTriageOrHigher({role_name: 'read', user: {permissions: {pull: true}}}), false);
    assert.equal(hasTriageOrHigher(null), false);
  });
});
