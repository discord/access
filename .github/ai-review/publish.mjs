#!/usr/bin/env node
// Publisher for the AI review gate.
//
// Runs in the job that holds the pull-request write token and never calls the model. It treats
// the review as untrusted input: every finding must match the schema and point at a line the
// pull request changed, credential-shaped output is refused outright, and nothing is posted if
// the pull request moved since the review ran. A review identical to one already posted for the
// same head is not posted again, and a complete review hides the summaries it supersedes and
// resolves earlier inline threads. A failed review keeps one "unavailable" notice per pull
// request, edited in place on later failures and hidden once a review is published.

import fs from 'node:fs';
import {createHash} from 'node:crypto';
import path from 'node:path';
import process from 'node:process';
import {pathToFileURL} from 'node:url';

import {createGitHub} from './github.mjs';
import {findSecretLikeText} from './secrets.mjs';

/** Marks the gate's own comments and reviews so later runs can find them. */
export const MARKER = '<!-- access-ai-review -->';
/** Marks the gate's "review unavailable" notice, which is edited in place rather than repeated. */
export const UNAVAILABLE_MARKER = '<!-- access-ai-review-unavailable -->';
const BOT_LOGIN = 'github-actions';
const MAX_FINDINGS = 10;
const BLOCKER_CONFIDENCE = 0.9;
const HISTORY_WINDOW = 100;
// Cap each cleanup pass at the maximum findings per review.
const CLEANUP_LIMIT = MAX_FINDINGS;

/** Finding classifications, their display labels, and the risk area each one implies. */
export const CLASSIFICATIONS = new Map([
  ['authz_tier', {label: 'Authorization tier', risk: 'auth'}],
  ['resolved_target_authz', {label: 'Resolved-target authorization', risk: 'auth'}],
  ['tag_constraint_bypass', {label: 'Tag-constraint bypass', risk: 'tags'}],
  ['approver_routing', {label: 'Approver routing', risk: 'routing'}],
  ['missing_active_filter', {label: 'Missing active-record filter', risk: 'data-filters'}],
  ['async_orm', {label: 'Async ORM', risk: 'async-orm'}],
  ['plugin_api_compat', {label: 'Plugin or API compatibility', risk: 'plugin-api'}],
  ['migration_safety', {label: 'Migration safety', risk: 'migration'}],
  ['audit_event_contract', {label: 'Audit-log contract', risk: 'audit'}],
  ['operator_leak', {label: 'Operator-specific logic', risk: 'operator-leak'}],
  ['frontend_authz_drift', {label: 'Frontend authorization drift', risk: 'auth'}],
  ['spec_mismatch', {label: 'Spec mismatch', risk: null}],
  ['runtime_correctness', {label: 'Correctness', risk: null}],
  ['runtime_security', {label: 'Security', risk: null}],
]);

/** Risk areas the publisher manages as `risk:<area>` labels. */
export const RISK_AREAS = Object.freeze([
  'auth',
  'tags',
  'routing',
  'data-filters',
  'async-orm',
  'plugin-api',
  'migration',
  'audit',
  'operator-leak',
]);
const RISK_LABEL_PREFIX = 'risk:';

function sanitize(value, limit) {
  return String(value)
    .replaceAll('<!--', '&lt;!--')
    .replaceAll('-->', '--&gt;')
    .replaceAll('@', '@​')
    .slice(0, limit)
    .trim();
}

/**
 * Parse the added (head) and deleted (base) line numbers from a unified-diff patch.
 *
 * @param {string | undefined} patch - A file's `patch` from the pull request files API.
 * @returns {{base: Set<number>, head: Set<number>}}
 */
export function changedLinesFromPatch(patch) {
  const changed = {base: new Set(), head: new Set()};
  if (typeof patch !== 'string') return changed;
  let inHunk = false;
  let oldLine = 0;
  let newLine = 0;
  for (const text of patch.split('\n')) {
    const hunk = text.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/u);
    if (hunk) {
      inHunk = true;
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
      continue;
    }
    if (!inHunk) continue;
    if (text.startsWith('+')) {
      changed.head.add(newLine);
      newLine += 1;
    } else if (text.startsWith('-')) {
      changed.base.add(oldLine);
      oldLine += 1;
    } else if (!text.startsWith('\\ No newline')) {
      oldLine += 1;
      newLine += 1;
    }
  }
  return changed;
}

function isObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function validateLocation(finding, changed) {
  if (typeof finding.path !== 'string' || !finding.path) throw new Error('needs a path');
  if (finding.side !== 'base' && finding.side !== 'head') throw new Error('has an invalid side');
  if (!Number.isInteger(finding.line) || finding.line < 1) throw new Error('needs a line');
  const startLine = finding.start_line ?? null;
  if (startLine !== null && (!Number.isInteger(startLine) || startLine < 1 || startLine >= finding.line)) {
    throw new Error('has a start_line that is not before line');
  }
  const changedLines = changed.get(`${finding.side}:${finding.path}`);
  if (!changedLines) throw new Error('points outside the changed files');
  for (let line = startLine ?? finding.line; line <= finding.line; line += 1) {
    if (!changedLines.has(line)) throw new Error(`does not point only at changed ${finding.side} lines`);
  }
  return {path: finding.path, side: finding.side, startLine, line: finding.line};
}

function validateSuggestion(finding) {
  const suggestion = finding.suggestion ?? null;
  if (suggestion === null) return null;
  if (typeof suggestion !== 'string') throw new Error('has an invalid suggestion');
  // A suggestion can only replace head-side lines, and a fence inside it would end the block.
  if (finding.side !== 'head' || suggestion.includes('```')) return null;
  return suggestion.slice(0, 2000);
}

/**
 * Validate raw model output against the schema rules the publisher depends on.
 *
 * @param {unknown} review - The parsed review file.
 * @param {object[]} files - The pull request's files from the GitHub API.
 * @returns {{spec_available: boolean, focus: {path: string, reason: string}[], riskAreas: string[],
 *   findings: object[], rejected: string[]}} Findings that pass, with severity downgraded to
 *   advisory below the blocker confidence bar, and a description of each finding that failed.
 * @throws {Error} If the review's overall shape is wrong or it contains credential-shaped text.
 */
export function validateReview(review, files) {
  if (!isObject(review)) throw new Error('review output must be an object');
  const secretMatches = findSecretLikeText(JSON.stringify(review));
  if (secretMatches.length) {
    throw new Error(`review output contained credential-shaped text (${secretMatches.join(', ')})`);
  }
  if (
    typeof review.spec_available !== 'boolean' ||
    !Array.isArray(review.findings) ||
    !Array.isArray(review.focus) ||
    !Array.isArray(review.risk_areas)
  ) {
    throw new Error('review output must contain spec_available, focus, risk_areas, and findings');
  }
  if (review.findings.length > MAX_FINDINGS) throw new Error(`review output contains more than ${MAX_FINDINGS} findings`);

  const changedPaths = new Set();
  const changed = new Map();
  for (const file of files) {
    const lines = changedLinesFromPatch(file.patch);
    changed.set(`head:${file.filename}`, lines.head);
    changed.set(`base:${file.previous_filename ?? file.filename}`, lines.base);
    changedPaths.add(file.filename);
  }

  const focus = review.focus
    .filter((item) => isObject(item) && changedPaths.has(item.path) && typeof item.reason === 'string')
    .slice(0, 5)
    .map((item) => ({path: item.path, reason: sanitize(item.reason, 300)}));
  const riskAreas = new Set(review.risk_areas.filter((area) => RISK_AREAS.includes(area)));

  const rejected = [];
  const findings = [];
  for (const [index, finding] of review.findings.entries()) {
    try {
      if (!isObject(finding)) throw new Error('must be an object');
      const classification = CLASSIFICATIONS.get(finding.classification);
      if (!classification) throw new Error('has an unknown classification');
      if (finding.severity !== 'blocker' && finding.severity !== 'advisory') throw new Error('has an invalid severity');
      if (typeof finding.confidence !== 'number' || finding.confidence < 0 || finding.confidence > 1) {
        throw new Error('has an invalid confidence');
      }
      if (typeof finding.title !== 'string' || !finding.title || typeof finding.body !== 'string' || !finding.body) {
        throw new Error('needs a title and body');
      }
      if (finding.classification === 'spec_mismatch' && !review.spec_available) {
        throw new Error('is a spec mismatch, but spec_available is false');
      }
      const location = validateLocation(finding, changed);
      findings.push({
        classification: finding.classification,
        severity: finding.severity === 'blocker' && finding.confidence >= BLOCKER_CONFIDENCE ? 'blocker' : 'advisory',
        confidence: finding.confidence,
        title: sanitize(finding.title, 160),
        body: sanitize(finding.body, 2000),
        suggestion: validateSuggestion(finding),
        ...location,
      });
      if (classification.risk) riskAreas.add(classification.risk);
    } catch (error) {
      rejected.push(`finding ${index + 1} ${error.message}`);
    }
  }
  findings.sort((left, right) => Number(right.severity === 'blocker') - Number(left.severity === 'blocker'));
  return {
    spec_available: review.spec_available,
    focus,
    riskAreas: RISK_AREAS.filter((area) => riskAreas.has(area)),
    findings,
    rejected,
  };
}

function fileLink(repository, baseSha, headSha, finding) {
  const encodedPath = finding.path.split('/').map(encodeURIComponent).join('/');
  const sha = finding.side === 'base' ? baseSha : headSha;
  const anchor = finding.startLine ? `L${finding.startLine}-L${finding.line}` : `L${finding.line}`;
  const label = finding.startLine ? `${finding.path}:${finding.startLine}-${finding.line}` : `${finding.path}:${finding.line}`;
  return `[${label}](https://github.com/${repository}/blob/${sha}/${encodedPath}#${anchor})`;
}

function plural(count, singular, pluralForm = `${singular}s`) {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

/**
 * Render the review summary.
 *
 * @param {object} review - Output of `validateReview`.
 * @param {string} repository - `owner/name`.
 * @param {string} baseSha
 * @param {string} headSha
 * @param {boolean} enforced - Whether blockers fail the check.
 * @returns {string} Markdown, starting with `MARKER`.
 */
export function formatComment(review, repository, baseSha, headSha, enforced) {
  const blockers = review.findings.filter((finding) => finding.severity === 'blocker');
  const rejected = review.rejected.length;
  let status;
  if (rejected && enforced) status = `Review invalid: ${plural(rejected, 'finding')} failed validation`;
  else if (blockers.length && enforced) status = `Changes required: ${plural(blockers.length, 'blocker')}`;
  else if (blockers.length) status = `Advisory mode: ${plural(blockers.length, 'blocker')} would fail the gate`;
  else status = review.findings.length ? `No blockers; ${plural(review.findings.length, 'advisory finding')}` : 'No findings';

  const lines = [MARKER, '## AI review', '', `**${status}.**`, `Reviewed commit \`${headSha}\`.`];

  lines.push('', '### Where reviewers should focus', '');
  if (review.focus.length) {
    for (const item of review.focus) lines.push(`- \`${item.path}\`: ${item.reason}`);
  } else {
    lines.push('Nothing in this change stood out as needing extra attention.');
  }
  if (review.riskAreas.length) {
    lines.push('', `Risk areas: ${review.riskAreas.map((area) => `\`${RISK_LABEL_PREFIX}${area}\``).join(', ')}.`);
  }

  lines.push('', '### Findings', '');
  if (review.findings.length) {
    for (const finding of review.findings) {
      const severity = finding.severity === 'blocker' ? 'Blocker' : 'Advisory';
      const {label} = CLASSIFICATIONS.get(finding.classification);
      lines.push(
        `- **${severity} / ${label}: ${finding.title}** (${Math.round(finding.confidence * 100)}% confidence, ${fileLink(repository, baseSha, headSha, finding)})`,
      );
    }
    lines.push('', 'Details are inline.');
  } else {
    lines.push('No issue met the reporting bar.');
  }
  if (!review.spec_available) lines.push('', 'No linked issue or stated requirements were available to check against.');

  lines.push(
    '',
    enforced
      ? 'Blockers above fail this check. Push a fix to run the review again.'
      : 'The gate is advisory: findings never fail the check. Set the repository variable `AI_REVIEW_ENFORCE=true` to make blockers fail it.',
  );
  if (rejected) {
    lines.push('', `<sub>${enforced ? 'The gate failed because' : 'Ignored'} ${plural(rejected, 'finding')} whose location or fields did not pass validation.</sub>`);
  }
  lines.push('', '<sub>Automated review. A maintainer still reviews every pull request.</sub>');
  return lines.join('\n');
}

/**
 * Render an inline comment for one finding.
 *
 * @param {object} finding - A validated finding.
 * @returns {string}
 */
export function formatInlineComment(finding) {
  const severity = finding.severity === 'blocker' ? 'Blocker' : 'Advisory';
  const {label} = CLASSIFICATIONS.get(finding.classification);
  const parts = [MARKER, `**${severity} / ${label}: ${finding.title}**`, '', finding.body];
  if (finding.suggestion !== null) parts.push('', '```suggestion', finding.suggestion.replace(/\n$/u, ''), '```');
  return parts.join('\n');
}

/**
 * Render the notice posted when no review could be produced.
 *
 * @param {string} reason - Why the review is unavailable. Withheld if it looks like a credential.
 * @param {boolean} enforced - Whether the gate fails when no review is available.
 * @param {string} [headSha] - The commit the failed review was for, shown so an edited notice
 *   says which push it describes.
 * @returns {string} Markdown that starts with `MARKER` and carries `UNAVAILABLE_MARKER`.
 */
export function formatUnavailable(reason, enforced, headSha = '') {
  const safeReason = findSecretLikeText(reason).length
    ? 'The failure message contained credential-shaped text and was withheld; see the workflow log.'
    : sanitize(reason, 500);
  return [
    MARKER,
    UNAVAILABLE_MARKER,
    '## AI review',
    '',
    enforced ? '**Review unavailable. The gate failed.**' : '**Review unavailable. The gate was left open.**',
    '',
    safeReason,
    ...(headSha ? ['', `Commit: ${headSha.slice(0, 12)}`] : []),
  ].join('\n');
}

/**
 * Compute the `risk:*` label changes for a pull request.
 *
 * @param {string[]} current - Label names currently on the pull request.
 * @param {string[]} riskAreas - Areas the review assigned.
 * @returns {{add: string[], remove: string[]}} Only labels in the managed `risk:` set change.
 */
export function riskLabelChanges(current, riskAreas) {
  const desired = new Set(riskAreas.map((area) => `${RISK_LABEL_PREFIX}${area}`));
  const managed = new Set(RISK_AREAS.map((area) => `${RISK_LABEL_PREFIX}${area}`));
  const present = new Set(current);
  return {
    add: [...desired].filter((label) => !present.has(label)),
    remove: current.filter((label) => managed.has(label) && !desired.has(label)),
  };
}

/**
 * The hidden fingerprint that identifies a published review, so a retry does not post it twice.
 *
 * @param {object} review - Output of `validateReview`.
 * @param {string} baseSha - The base the review analyzed.
 * @param {string} headSha - The head the review analyzed.
 * @param {boolean} enforced - Part of the fingerprint because it changes the rendered summary.
 * @returns {string} An HTML comment.
 */
export function reviewFingerprint(review, baseSha, headSha, enforced) {
  const digest = createHash('sha256').update(JSON.stringify({review, baseSha, headSha, enforced})).digest('hex');
  return `<!-- access-ai-review-result:${digest} -->`;
}

/**
 * Build the inline comments for the review API, mapping base-side paths of renamed files back to
 * the head path GitHub expects.
 *
 * @param {object[]} findings - Validated findings.
 * @param {object[]} files - The pull request's files.
 * @returns {object[]}
 */
export function reviewComments(findings, files) {
  return findings.map((finding) => {
    const filePath =
      finding.side === 'base'
        ? (files.find((file) => (file.previous_filename ?? file.filename) === finding.path)?.filename ?? finding.path)
        : finding.path;
    const side = finding.side === 'base' ? 'LEFT' : 'RIGHT';
    return {
      path: filePath,
      side,
      line: finding.line,
      ...(finding.startLine ? {start_line: finding.startLine, start_side: side} : {}),
      body: formatInlineComment(finding),
    };
  });
}

/** Fetch every file the pull request changes, following pagination. */
async function fetchPullFiles({github, repository, pullNumber}) {
  const files = [];
  for (let page = 1; ; page += 1) {
    const batch = await github.request(`/repos/${repository}/pulls/${pullNumber}/files?per_page=100&page=${page}`);
    files.push(...batch);
    if (batch.length < 100) return files;
  }
}

/** Fetch the last `HISTORY_WINDOW` nodes of one of the pull request's GraphQL connections. */
async function queryPullRequest({github, repository, pullNumber}, connection, selection) {
  const [owner, name] = repository.split('/');
  const data = await github.graphql(
    `query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) { ${connection} { nodes { ${selection} } } }
      }
    }`,
    {owner, name, number: pullNumber},
  );
  return data.repository.pullRequest[connection.split('(')[0]].nodes;
}

/** The gate's recent reviews on the pull request, oldest first. */
function recentReviews(pr) {
  return queryPullRequest(
    pr,
    `reviews(last: ${HISTORY_WINDOW}, author: "${BOT_LOGIN}[bot]")`,
    'id isMinimized body author { login __typename } commit { oid }',
  );
}

/** The pull request's recent review threads with their first comment, oldest first. */
function recentReviewThreads(pr) {
  return queryPullRequest(
    pr,
    `reviewThreads(last: ${HISTORY_WINDOW})`,
    'id isResolved comments(first: 1) { nodes { body author { login __typename } commit { oid } } }',
  );
}

/** The gate's visible "review unavailable" notices on the pull request, oldest first. */
async function visibleUnavailableNotices(pr) {
  const comments = await queryPullRequest(
    pr,
    `comments(last: ${HISTORY_WINDOW})`,
    'id databaseId isMinimized body author { login __typename }',
  );
  return comments.filter(
    (comment) => !comment.isMinimized && isOwnBot(comment.author) && comment.body?.includes(UNAVAILABLE_MARKER),
  );
}

/** Whether a GraphQL author is this workflow's own bot account. */
function isOwnBot(author) {
  return author?.login === BOT_LOGIN && author.__typename === 'Bot';
}

/** Hide a comment or review as outdated, logging rather than throwing on failure. */
async function minimize({github}, id, what) {
  try {
    await github.graphql(
      `mutation($id: ID!) { minimizeComment(input: { subjectId: $id, classifier: OUTDATED }) { minimizedComment { isMinimized } } }`,
      {id},
    );
  } catch (error) {
    console.warn(`Could not hide ${what} ${id}: ${error.message}`);
  }
}

/** Raised when the pull request's head or base moved after the review analyzed it. */
export class StaleReviewError extends Error {
  constructor() {
    super('the pull request changed since the review ran; refusing to publish a stale review');
    this.name = 'StaleReviewError';
  }
}

/** Fetch the pull request, throwing `StaleReviewError` if its head or base moved. */
async function assertUnmoved({github, repository, pullNumber, baseSha, headSha}) {
  const pull = await github.request(`/repos/${repository}/pulls/${pullNumber}`);
  if (pull.head.sha !== headSha || pull.base.sha !== baseSha) throw new StaleReviewError();
  return pull;
}

/** Bring the pull request's `risk:*` labels in line with the review, logging failures. */
async function syncRiskLabels({github, repository, pullNumber}, labels, riskAreas) {
  const {add, remove} = riskLabelChanges(
    labels.map((label) => label.name),
    riskAreas,
  );
  try {
    if (add.length) {
      await github.request(`/repos/${repository}/issues/${pullNumber}/labels`, {
        method: 'POST',
        body: JSON.stringify({labels: add}),
      });
    }
    for (const label of remove) {
      await github.request(`/repos/${repository}/issues/${pullNumber}/labels/${encodeURIComponent(label)}`, {
        method: 'DELETE',
      });
    }
  } catch (error) {
    console.warn(`Could not update risk labels: ${error.message}`);
  }
}

/**
 * Hide the gate's earlier summaries and unavailable notices, and resolve its inline threads from
 * earlier commits, once `publishedId` has replaced them. Stops if the pull request moves.
 */
async function cleanUpSuperseded(pr, publishedId) {
  let previousReviews;
  try {
    // Include reviews that arrived during publication, but preserve later ones.
    const history = await recentReviews(pr);
    await assertUnmoved(pr);
    const index = history.findIndex((previous) => previous.id === publishedId);
    if (index === -1) {
      console.warn('Published review is absent from recent history; skipping cleanup');
      return;
    }
    previousReviews = history.slice(0, index);
  } catch (error) {
    console.warn(`Skipping cleanup: ${error.message}`);
    return;
  }
  let minimized = 0;
  for (const previous of [...previousReviews].reverse()) {
    if (previous.isMinimized || !isOwnBot(previous.author) || !previous.body?.startsWith(MARKER)) continue;
    if (minimized >= CLEANUP_LIMIT) break;
    minimized += 1;
    await minimize(pr, previous.id, 'superseded review');
  }
  try {
    for (const notice of await visibleUnavailableNotices(pr)) await minimize(pr, notice.id, 'unavailable notice');
  } catch (error) {
    console.warn(`Could not hide unavailable notices: ${error.message}`);
  }
  try {
    const threads = await recentReviewThreads(pr);
    await assertUnmoved(pr);
    let attempted = 0;
    for (const thread of [...threads].reverse()) {
      const first = thread.comments.nodes[0];
      if (thread.isResolved || !isOwnBot(first?.author) || !first.body.startsWith(MARKER)) continue;
      if (first.commit?.oid === pr.headSha) continue;
      if (attempted >= CLEANUP_LIMIT) break;
      attempted += 1;
      try {
        await pr.github.graphql(`mutation($id: ID!) { resolveReviewThread(input: { threadId: $id }) { thread { id } } }`, {
          id: thread.id,
        });
      } catch (error) {
        console.warn(`Could not resolve superseded thread ${thread.id}: ${error.message}`);
      }
    }
  } catch (error) {
    console.warn(`Could not clean up superseded review threads: ${error.message}`);
  }
}

/**
 * Publish a validated review as a pull request review with inline comments.
 *
 * @param {object} options
 * @param {{request: Function, graphql: Function}} options.github - From `createGitHub`.
 * @param {string} options.repository - `owner/name`.
 * @param {number} options.pullNumber
 * @param {string} options.baseSha - The base the review analyzed.
 * @param {string} options.headSha - The head the review analyzed.
 * @param {object} options.review - Output of `validateReview`.
 * @param {object[]} options.files - The pull request's files.
 * @param {boolean} options.enforced
 * @returns {Promise<{published: boolean}>} `published` is false when an identical review was
 *   already posted for this head.
 * @throws {StaleReviewError} If the pull request moved since the review ran.
 */
export async function publishReview({github, repository, pullNumber, baseSha, headSha, review, files, enforced}) {
  const pr = {github, repository, pullNumber, baseSha, headSha};
  await assertUnmoved(pr);
  const fingerprint = reviewFingerprint(review, baseSha, headSha, enforced);
  const reviews = await recentReviews(pr);
  const existing = reviews.findLast(
    (previous) => isOwnBot(previous.author) && previous.commit?.oid === headSha && previous.body?.includes(fingerprint),
  );
  let publishedId = existing?.id;
  if (!existing) {
    const pull = await assertUnmoved(pr);
    const published = await github.request(`/repos/${repository}/pulls/${pullNumber}/reviews`, {
      method: 'POST',
      body: JSON.stringify({
        commit_id: headSha,
        event: 'COMMENT',
        body: `${formatComment(review, repository, baseSha, headSha, enforced)}\n\n${fingerprint}`,
        comments: reviewComments(review.findings, files),
      }),
    });
    publishedId = published.node_id;
    await syncRiskLabels(pr, pull.labels ?? [], review.riskAreas);
  }
  // A review with rejected findings is incomplete, so it must not supersede earlier ones.
  if (!review.rejected.length) await cleanUpSuperseded(pr, publishedId);
  return {published: !existing};
}

/**
 * Post or update the pull request's "review unavailable" notice.
 *
 * Edits the latest visible notice in place rather than adding another, so a persistent failure
 * (an expired key, a misconfigured provider) leaves one notice instead of one per push.
 *
 * @param {object} options
 * @param {{request: Function, graphql: Function}} options.github - From `createGitHub`.
 * @param {string} options.repository - `owner/name`.
 * @param {number} options.pullNumber
 * @param {string} options.baseSha - The base the failed review was for.
 * @param {string} options.headSha - The head the failed review was for.
 * @param {string} options.reason - Passed to `formatUnavailable`.
 * @param {boolean} options.enforced
 * @returns {Promise<{updated: boolean}>} `updated` is true when an existing notice was edited.
 * @throws {StaleReviewError} If the pull request moved since the review ran; nothing is posted.
 */
export async function postUnavailableNotice({github, repository, pullNumber, baseSha, headSha, reason, enforced}) {
  const pr = {github, repository, pullNumber, baseSha, headSha};
  await assertUnmoved(pr);
  const body = JSON.stringify({body: formatUnavailable(reason, enforced, headSha)});
  const latest = (await visibleUnavailableNotices(pr)).at(-1);
  if (latest) {
    await github.request(`/repos/${repository}/issues/comments/${latest.databaseId}`, {method: 'PATCH', body});
    return {updated: true};
  }
  await github.request(`/repos/${repository}/issues/${pullNumber}/comments`, {method: 'POST', body});
  return {updated: false};
}

/**
 * The publisher's result for a run where no review ran.
 *
 * @param {string | undefined} check - The gate's `check` output; empty when the gate job failed.
 * @param {boolean} enforced
 * @returns {{fail: boolean, message: string}} `fail` is true only for an enforced gate whose
 *   review is still owed: one awaiting the label, or one whose gate never decided.
 */
export function unreviewedOutcome(check, enforced) {
  if (check === 'pass' || check === 'ignore') return {fail: false, message: `Nothing to review (${check}).`};
  const message =
    check === 'awaiting-label'
      ? 'The author lacks triage access, so the review waits for someone with triage access to add the ai-review label.'
      : 'The gate job did not decide whether to review; see its log.';
  return {fail: enforced, message: `${message}${enforced ? ' Failing the gate.' : ''}`};
}

/** Publish the review job's result, or the notice explaining why there is none. */
async function main() {
  const env = process.env;
  const repository = env.GITHUB_REPOSITORY;
  const pullNumber = Number(env.PR_NUMBER);
  const {BASE_SHA: baseSha, HEAD_SHA: headSha, REVIEW_FILE: reviewFile, GITHUB_TOKEN: token, CHECK: check} = env;
  const enforced = env.ENFORCE === 'true';
  if (check !== 'review') {
    const {fail, message} = unreviewedOutcome(check, enforced);
    console.log(message);
    if (fail) process.exitCode = 1;
    return;
  }
  if (!reviewFile || !repository || !pullNumber || !baseSha || !headSha || !token) {
    throw new Error('missing required review publisher environment');
  }
  const pr = {github: createGitHub(token), repository, pullNumber, baseSha, headSha};
  // A newer push starts its own review, and a labeled run can be requested again, so a stale run
  // posts nothing. It still fails an enforced gate, which the newer run then decides.
  const refuseStale = (error) => {
    if (!(error instanceof StaleReviewError)) throw error;
    console.warn(`Not publishing: ${error.message}`);
    if (enforced) process.exitCode = 1;
  };
  const postUnavailable = async (reason) => {
    console.warn(`${reason} ${enforced ? 'Failing the gate.' : 'Leaving the gate open.'}`);
    if (enforced) process.exitCode = 1;
    await postUnavailableNotice({...pr, reason, enforced}).catch(refuseStale);
  };

  if (!fs.existsSync(reviewFile)) {
    await postUnavailable(env.REVIEW_ERROR || 'The agent did not produce a review. Check the workflow log.');
    return;
  }
  let review;
  let files;
  try {
    files = await fetchPullFiles(pr);
    review = validateReview(JSON.parse(fs.readFileSync(reviewFile, 'utf8')), files);
  } catch (error) {
    await postUnavailable(`The review output was invalid: ${error.message}`);
    return;
  }
  let published;
  try {
    ({published} = await publishReview({...pr, review, files, enforced}));
  } catch (error) {
    refuseStale(error);
    return;
  }
  console.log(
    `${published ? 'Published' : 'Already published'}: ${review.findings.length} findings, ${review.rejected.length} rejected`,
  );
  for (const reason of review.rejected) console.warn(`Rejected ${reason}`);
  const blockers = review.findings.filter((finding) => finding.severity === 'blocker');
  if (enforced && (blockers.length || review.rejected.length)) process.exitCode = 1;
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;
if (isMain) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
