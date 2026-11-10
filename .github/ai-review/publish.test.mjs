import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {describe, it} from 'node:test';
import {fileURLToPath} from 'node:url';

import {
  CLASSIFICATIONS,
  MARKER,
  UNAVAILABLE_MARKER,
  RISK_AREAS,
  StaleReviewError,
  changedLinesFromPatch,
  formatComment,
  formatInlineComment,
  formatUnavailable,
  postUnavailableNotice,
  publishReview,
  reviewComments,
  reviewFingerprint,
  riskLabelChanges,
  unreviewedOutcome,
  validateReview,
} from './publish.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const BASE = 'b'.repeat(40);
const HEAD = 'a'.repeat(40);
const REPO = 'owner/access';

const files = [
  {filename: 'api/routers/groups.py', patch: '@@ -10,3 +10,4 @@ def x():\n context\n-old_line\n+new_line\n+second_new\n tail'},
  {filename: 'api/new_name.py', previous_filename: 'api/old_name.py', patch: '@@ -1,2 +1,2 @@\n-removed\n+added\n same'},
];

function finding(overrides = {}) {
  return {
    classification: 'authz_tier',
    severity: 'advisory',
    confidence: 0.8,
    title: 'Missing owner check',
    body: 'The handler skips `can_manage_group`.',
    path: 'api/routers/groups.py',
    side: 'head',
    start_line: null,
    line: 11,
    suggestion: null,
    ...overrides,
  };
}

function rawReview(overrides = {}) {
  return {spec_available: true, focus: [], risk_areas: [], findings: [], ...overrides};
}

describe('schema and publisher stay in sync', () => {
  it('uses the same classification and risk-area enums', async () => {
    const schema = JSON.parse(await fs.readFile(path.join(here, 'schema.json'), 'utf8'));
    const item = schema.properties.findings.items;
    assert.deepEqual(item.properties.classification.enum, [...CLASSIFICATIONS.keys()]);
    assert.deepEqual(schema.properties.risk_areas.items.enum, [...RISK_AREAS]);
    assert.equal(schema.properties.findings.maxItems, 10);
    assert.deepEqual([...item.required].sort(), Object.keys(item.properties).sort());
  });
});

describe('changedLinesFromPatch', () => {
  it('maps added and deleted lines', () => {
    const lines = changedLinesFromPatch(files[0].patch);
    assert.deepEqual([...lines.head], [11, 12]);
    assert.deepEqual([...lines.base], [11]);
    assert.deepEqual([...changedLinesFromPatch(undefined).head], []);
  });
});

describe('validateReview', () => {
  it('keeps anchored findings and downgrades low-confidence blockers', () => {
    const review = validateReview(
      rawReview({
        findings: [
          finding({severity: 'blocker', confidence: 0.89}),
          finding({severity: 'blocker', confidence: 0.95, start_line: 11, line: 12}),
        ],
      }),
      files,
    );
    assert.deepEqual(
      review.findings.map((item) => item.severity),
      ['blocker', 'advisory'],
    );
    assert.equal(review.findings[0].startLine, 11);
    assert.deepEqual(review.rejected, []);
  });

  it('rejects findings that are not anchored to changed lines', () => {
    const review = validateReview(
      rawReview({
        findings: [
          finding({line: 13}),
          finding({path: 'README.md'}),
          finding({side: 'base', line: 12}),
          finding({start_line: 10, line: 11}),
          finding({start_line: 12, line: 11}),
        ],
      }),
      files,
    );
    assert.equal(review.findings.length, 0);
    assert.equal(review.rejected.length, 5);
  });

  it('anchors base-side findings on renamed files to the old path', () => {
    const review = validateReview(rawReview({findings: [finding({path: 'api/old_name.py', side: 'base', line: 1})]}), files);
    assert.equal(review.findings.length, 1);
    assert.equal(reviewComments(review.findings, files)[0].path, 'api/new_name.py');
    assert.equal(reviewComments(review.findings, files)[0].side, 'LEFT');
  });

  it('rejects unknown classifications and spec findings without a spec', () => {
    const review = validateReview(
      rawReview({spec_available: false, findings: [finding({classification: 'style'}), finding({classification: 'spec_mismatch'})]}),
      files,
    );
    assert.equal(review.findings.length, 0);
    assert.match(review.rejected[0], /unknown classification/u);
    assert.match(review.rejected[1], /spec_available is false/u);
  });

  it('refuses the whole review when it contains credential-shaped text', () => {
    assert.throws(
      () => validateReview(rawReview({findings: [finding({body: 'key sk-ant-api03-abcdefghijklmnopqrstuv'})]}), files),
      /credential-shaped text \(anthropic-api-key\)/u,
    );
    assert.throws(
      () => validateReview(rawReview({focus: [{path: 'api/routers/groups.py', reason: 'ghp_' + 'a'.repeat(36)}]}), files),
      /github-token/u,
    );
  });

  it('caps findings and requires the overall shape', () => {
    assert.throws(() => validateReview(rawReview({findings: Array.from({length: 11}, () => finding())}), files), /more than 10/u);
    assert.throws(() => validateReview({findings: []}, files), /must contain/u);
    assert.throws(() => validateReview([], files), /must be an object/u);
  });

  it('keeps suggestions only for head-side lines without fences', () => {
    const review = validateReview(
      rawReview({
        findings: [
          finding({suggestion: 'fixed_line'}),
          finding({suggestion: '```\nbreakout'}),
          finding({path: 'api/old_name.py', side: 'base', line: 1, suggestion: 'x'}),
        ],
      }),
      files,
    );
    assert.deepEqual(
      review.findings.map((item) => item.suggestion),
      ['fixed_line', null, null],
    );
  });

  it('derives risk areas from findings and filters focus to changed paths', () => {
    const review = validateReview(
      rawReview({
        risk_areas: ['migration', 'not-an-area'],
        focus: [
          {path: 'api/routers/groups.py', reason: 'changes who may approve; ping @someone'},
          {path: 'elsewhere.py', reason: 'not changed'},
        ],
        findings: [finding({classification: 'missing_active_filter'})],
      }),
      files,
    );
    assert.deepEqual(review.riskAreas, ['data-filters', 'migration']);
    assert.equal(review.focus.length, 1);
    assert.match(review.focus[0].reason, /@​someone/u);
  });

  it('neutralizes HTML comments so output cannot forge the marker', () => {
    const review = validateReview(rawReview({findings: [finding({body: `${MARKER} hidden -->`})]}), files);
    assert.ok(!review.findings[0].body.includes('<!--'));
  });
});

describe('formatting', () => {
  const review = validateReview(
    rawReview({
      focus: [{path: 'api/routers/groups.py', reason: 'changes the authorization tier for group edits'}],
      risk_areas: ['auth'],
      findings: [finding({severity: 'blocker', confidence: 0.95, suggestion: 'new_line_fixed\n'})],
    }),
    files,
  );

  it('leads with where reviewers should focus and states advisory mode', () => {
    const body = formatComment(review, REPO, BASE, HEAD, false);
    assert.ok(body.startsWith(MARKER));
    assert.match(body, /### Where reviewers should focus\n\n- `api\/routers\/groups.py`: changes the authorization/u);
    assert.match(body, /Advisory mode: 1 blocker would fail the gate/u);
    assert.match(body, /`risk:auth`/u);
    assert.match(body, /blob\/a{40}\/api\/routers\/groups.py#L11/u);
    assert.match(formatComment(review, REPO, BASE, HEAD, true), /Changes required: 1 blocker/u);
  });

  it('renders a suggestion block inline', () => {
    const body = formatInlineComment(review.findings[0]);
    assert.match(body, /```suggestion\nnew_line_fixed\n```$/u);
    assert.match(body, /Blocker \/ Authorization tier/u);
  });

  it('marks the unavailable notice and names its commit', () => {
    const body = formatUnavailable('agent timed out', false, HEAD);
    assert.ok(body.startsWith(MARKER));
    assert.ok(body.includes(UNAVAILABLE_MARKER));
    assert.ok(body.endsWith(`Commit: ${HEAD.slice(0, 12)}`));
  });

  it('withholds credential-shaped failure reasons', () => {
    assert.match(formatUnavailable('upstream said sk-ant-api03-abcdefghijklmnopqrstuvwx', false), /withheld/u);
    assert.match(formatUnavailable('agent timed out', true), /gate failed[\s\S]*agent timed out/u);
  });
});

describe('riskLabelChanges', () => {
  it('adds desired labels and removes only stale managed ones', () => {
    assert.deepEqual(riskLabelChanges(['risk:auth', 'risk:migration', 'bug', 'risk:custom'], ['auth', 'tags']), {
      add: ['risk:tags'],
      remove: ['risk:migration'],
    });
  });
});

function fakeGitHub({head = HEAD, reviews = [], threads = [], comments = []} = {}) {
  const calls = [];
  return {
    calls,
    async request(route, options = {}) {
      calls.push({route, method: options.method ?? 'GET', body: options.body ? JSON.parse(options.body) : undefined});
      if (route === `/repos/${REPO}/pulls/7`) return {head: {sha: head}, base: {sha: BASE}, labels: [{name: 'risk:migration'}]};
      if (route.endsWith('/reviews')) return {node_id: 'NEW'};
      return {};
    },
    async graphql(query, variables) {
      calls.push({graphql: query.trim().split(/\s+/u)[0], variables});
      if (query.includes('reviewThreads(')) return {repository: {pullRequest: {reviewThreads: {nodes: threads}}}};
      if (query.includes('comments(last')) return {repository: {pullRequest: {comments: {nodes: comments}}}};
      if (query.includes('reviews(')) {
        const nodes = calls.some((call) => call.method === 'POST' && call.route?.endsWith('/reviews'))
          ? [...reviews, {id: 'NEW', body: MARKER, author: {login: 'github-actions', __typename: 'Bot'}}]
          : reviews;
        return {repository: {pullRequest: {reviews: {nodes}}}};
      }
      return {};
    },
  };
}

describe('publishReview', () => {
  const review = validateReview(rawReview({risk_areas: ['auth'], findings: [finding()]}), files);
  const options = {repository: REPO, pullNumber: 7, baseSha: BASE, headSha: HEAD, review, files, enforced: false};
  const bot = {login: 'github-actions', __typename: 'Bot'};

  it('refuses to publish when the head moved', async () => {
    const github = fakeGitHub({head: 'c'.repeat(40)});
    await assert.rejects(publishReview({github, ...options}), StaleReviewError);
    assert.ok(!github.calls.some((call) => call.method === 'POST'));
  });

  it('posts one review, syncs risk labels, and hides superseded summaries', async () => {
    const github = fakeGitHub({
      reviews: [
        {id: 'OLD', body: `${MARKER}\nold`, author: bot, commit: {oid: 'd'.repeat(40)}},
        {id: 'HUMAN', body: `${MARKER}\nquoted`, author: {login: 'someone', __typename: 'User'}},
      ],
      threads: [
        {id: 'T1', isResolved: false, comments: {nodes: [{body: `${MARKER}\nx`, author: bot, commit: {oid: 'd'.repeat(40)}}]}},
        {id: 'T2', isResolved: false, comments: {nodes: [{body: `${MARKER}\nx`, author: bot, commit: {oid: HEAD}}]}},
      ],
    });
    assert.deepEqual(await publishReview({github, ...options}), {published: true});
    const post = github.calls.find((call) => call.method === 'POST' && call.route?.endsWith('/reviews'));
    assert.equal(post.body.commit_id, HEAD);
    assert.equal(post.body.event, 'COMMENT');
    assert.equal(post.body.comments.length, 1);
    assert.ok(post.body.body.includes(reviewFingerprint(review, BASE, HEAD, false)));
    const labelAdd = github.calls.find((call) => call.route?.endsWith('/issues/7/labels'));
    assert.deepEqual(labelAdd.body, {labels: ['risk:auth']});
    assert.ok(github.calls.some((call) => call.method === 'DELETE' && call.route?.endsWith('/labels/risk%3Amigration')));
    const minimized = github.calls.filter((call) => call.graphql?.startsWith('mutation') && call.variables.id === 'OLD');
    assert.equal(minimized.length, 1);
    assert.ok(!github.calls.some((call) => call.variables?.id === 'HUMAN'));
    const resolved = github.calls.filter((call) => call.graphql?.startsWith('mutation') && call.variables.id?.startsWith('T'));
    assert.deepEqual(
      resolved.map((call) => call.variables.id),
      ['T1'],
    );
  });

  it('does not post a review that is already published for this head', async () => {
    const fingerprint = reviewFingerprint(review, BASE, HEAD, false);
    const github = fakeGitHub({reviews: [{id: 'SAME', body: `${MARKER}\n${fingerprint}`, author: bot, commit: {oid: HEAD}}]});
    assert.deepEqual(await publishReview({github, ...options}), {published: false});
    assert.ok(!github.calls.some((call) => call.method === 'POST'));
  });

  it('hides visible unavailable notices once a review is published', async () => {
    const github = fakeGitHub({
      comments: [
        {id: 'N1', databaseId: 1, isMinimized: false, body: `${MARKER}\n${UNAVAILABLE_MARKER}`, author: bot},
        {id: 'N2', databaseId: 2, isMinimized: true, body: `${MARKER}\n${UNAVAILABLE_MARKER}`, author: bot},
        {id: 'C3', databaseId: 3, isMinimized: false, body: UNAVAILABLE_MARKER, author: {login: 'x', __typename: 'User'}},
      ],
    });
    await publishReview({github, ...options});
    const hidden = github.calls.filter((call) => call.graphql?.startsWith('mutation')).map((call) => call.variables.id);
    assert.deepEqual(hidden, ['N1']);
  });

  it('leaves earlier reviews alone when this one had rejected findings', async () => {
    const partial = {...review, rejected: ['finding 2 points outside the changed files']};
    const github = fakeGitHub({reviews: [{id: 'OLD', body: `${MARKER}\nold`, author: bot, commit: {oid: 'd'.repeat(40)}}]});
    await publishReview({github, ...options, review: partial});
    assert.ok(!github.calls.some((call) => call.graphql?.startsWith('mutation')));
  });
});

describe('postUnavailableNotice', () => {
  const options = {repository: REPO, pullNumber: 7, baseSha: BASE, headSha: HEAD, reason: 'agent timed out', enforced: false};
  const bot = {login: 'github-actions', __typename: 'Bot'};

  it('posts a notice when none is visible', async () => {
    const github = fakeGitHub();
    assert.deepEqual(await postUnavailableNotice({github, ...options}), {updated: false});
    const post = github.calls.find((call) => call.method === 'POST' && call.route.endsWith('/issues/7/comments'));
    assert.match(post.body.body, /agent timed out/u);
  });

  it('edits the latest visible notice instead of adding another', async () => {
    const notice = (databaseId) => ({id: `N${databaseId}`, databaseId, isMinimized: false, body: UNAVAILABLE_MARKER, author: bot});
    const github = fakeGitHub({comments: [notice(1), notice(2)]});
    assert.deepEqual(await postUnavailableNotice({github, ...options}), {updated: true});
    assert.ok(github.calls.some((call) => call.method === 'PATCH' && call.route === `/repos/${REPO}/issues/comments/2`));
    assert.ok(!github.calls.some((call) => call.method === 'POST' && call.route?.endsWith('/comments')));
  });

  it('posts nothing when the head moved', async () => {
    const github = fakeGitHub({head: 'c'.repeat(40)});
    await assert.rejects(postUnavailableNotice({github, ...options}), StaleReviewError);
    assert.ok(!github.calls.some((call) => call.method === 'POST' || call.method === 'PATCH'));
  });
});

describe('unreviewedOutcome', () => {
  it('fails an enforced gate only while a review is owed', () => {
    assert.equal(unreviewedOutcome('awaiting-label', true).fail, true);
    assert.equal(unreviewedOutcome('awaiting-label', false).fail, false);
    for (const check of ['pass', 'ignore']) assert.equal(unreviewedOutcome(check, true).fail, false, check);
    assert.equal(unreviewedOutcome('', true).fail, true);
    assert.equal(unreviewedOutcome(undefined, false).fail, false);
  });
});
