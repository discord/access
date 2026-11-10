#!/usr/bin/env node
// Decides whether the AI review gate runs for a pull_request_target event.
//
// The review runs automatically for pull requests whose author holds triage access or higher on
// the repository, and otherwise only when someone with triage access or higher adds the one-shot
// `ai-review` label. Repository permission comes from the API rather than the event's
// `author_association`, which under-reports members whose organization membership is private.
// Nothing runs, and nothing is posted, when the model credential is not configured.

import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import {pathToFileURL} from 'node:url';

import {GitHubError, createGitHub} from './github.mjs';

/** The label that requests a one-shot review. */
export const TRIGGER_LABEL = 'ai-review';
const AUTOMATIC_ACTIONS = new Set(['opened', 'reopened', 'synchronize', 'ready_for_review']);
const TRUSTED_ROLE_NAMES = new Set(['triage', 'write', 'maintain', 'admin']);

/**
 * Whether a collaborator-permission API response grants triage access or higher.
 *
 * Reads the `user.permissions` booleans first because they also resolve custom repository
 * roles to their base level, and falls back to the built-in `role_name`.
 *
 * @param {object | null} permission - The body of `GET /repos/{repo}/collaborators/{user}/permission`.
 * @returns {boolean}
 */
export function hasTriageOrHigher(permission) {
  const flags = permission?.user?.permissions;
  if (flags && (flags.triage || flags.push || flags.maintain || flags.admin)) return true;
  return TRUSTED_ROLE_NAMES.has(permission?.role_name);
}

/** Whether a GitHub user is a bot or app account. */
function isBot(user) {
  return user?.type === 'Bot' || String(user?.login ?? '').endsWith('[bot]');
}

/**
 * What the run reports on the gate's check.
 *
 * - `review`: a review runs, and its result decides the check.
 * - `pass`: there is nothing to review (a draft, a closed pull request, or a bot author); the
 *   check passes.
 * - `awaiting-label`: the author lacks triage access, so the review waits for the `ai-review`
 *   label; the check fails when the gate is enforced.
 * - `ignore`: the event says nothing about the pull request's contents (another label, or an
 *   `ai-review` label from someone without triage access); the run reports under a different
 *   check name so it cannot overwrite an earlier result.
 * - `disabled`: no model credential is configured or the event is unsupported; nothing runs and
 *   nothing is reported.
 */
export const CHECKS = Object.freeze(['review', 'pass', 'awaiting-label', 'ignore', 'disabled']);

/**
 * Decide whether to review.
 *
 * @param {object} options
 * @param {string} options.eventName - `GITHUB_EVENT_NAME`.
 * @param {object} options.event - The parsed event payload.
 * @param {boolean} options.hasKey - Whether the model credential secret is configured.
 * @param {(login: string) => Promise<boolean>} options.isTrusted - Resolves whether a user holds
 *   triage access or higher. Only called when the decision depends on it.
 * @returns {Promise<{run: boolean, check: string, reason: string, labeled: boolean, headSha?: string, baseSha?: string}>}
 *   `check` is one of `CHECKS`. `labeled` is true whenever the event added the `ai-review` label,
 *   so the label is removed even when no review runs. `headSha` is the head recorded in the
 *   event, which is what gets reviewed even if the pull request has moved since.
 */
export async function decide({eventName, event, hasKey, isTrusted}) {
  const pull = event?.pull_request;
  if (eventName !== 'pull_request_target' || !pull) {
    return {run: false, check: 'disabled', reason: `unsupported event ${eventName}`, labeled: false};
  }
  const labeled = event.action === 'labeled' && event.label?.name === TRIGGER_LABEL;
  const skip = (check, reason) => ({run: false, check, reason, labeled});
  if (!hasKey) return skip('disabled', 'no model credential is configured');
  if (event.action === 'labeled' && !labeled) {
    return skip('ignore', `label ${event.label?.name} is not ${TRIGGER_LABEL}`);
  }
  if (labeled) {
    const sender = event.sender?.login;
    if (!sender || isBot(event.sender) || !(await isTrusted(sender))) {
      return skip('ignore', `${sender} lacks triage access`);
    }
  } else if (!AUTOMATIC_ACTIONS.has(event.action)) {
    return skip('ignore', `action ${event.action} does not review`);
  }
  if (pull.state !== 'open') return skip('pass', 'pull request is not open');
  if (pull.draft) return skip('pass', 'pull request is a draft');

  const target = {headSha: pull.head.sha, baseSha: pull.base.sha};
  if (labeled) {
    return {run: true, check: 'review', reason: `${TRIGGER_LABEL} label added by ${event.sender.login}`, labeled, ...target};
  }
  const author = pull.user?.login;
  if (!author || isBot(pull.user)) return skip('pass', `author ${author} is a bot`);
  if (!(await isTrusted(author))) {
    return skip(
      'awaiting-label',
      `author ${author} lacks triage access; someone with triage access can add the ${TRIGGER_LABEL} label`,
    );
  }
  return {run: true, check: 'review', reason: `author ${author} holds triage access`, labeled, ...target};
}

/** Fetch a user's permission on the repository, or null when they are not a collaborator. */
async function fetchPermission(github, repository, login) {
  try {
    return await github.request(`/repos/${repository}/collaborators/${encodeURIComponent(login)}/permission`);
  } catch (error) {
    if (error instanceof GitHubError && error.status === 404) return null;
    throw error;
  }
}

async function main() {
  const {GITHUB_EVENT_NAME, GITHUB_EVENT_PATH, GITHUB_OUTPUT, GITHUB_REPOSITORY, GITHUB_TOKEN} = process.env;
  const event = JSON.parse(await fs.readFile(GITHUB_EVENT_PATH, 'utf8'));
  const github = createGitHub(GITHUB_TOKEN);
  const decision = await decide({
    eventName: GITHUB_EVENT_NAME,
    event,
    hasKey: process.env.HAS_API_KEY === 'true',
    isTrusted: async (login) => hasTriageOrHigher(await fetchPermission(github, GITHUB_REPOSITORY, login)),
  });
  // Logged so the association GitHub reports for private organization members is visible.
  console.log(`author_association=${event.pull_request?.author_association ?? 'unknown'}`);
  console.log(`${decision.run ? 'Reviewing' : 'Skipping'}: ${decision.reason}`);
  const outputs = {
    run: String(decision.run),
    check: decision.check,
    head_sha: decision.headSha ?? '',
    base_sha: decision.baseSha ?? '',
    labeled: String(decision.labeled),
  };
  await fs.appendFile(
    GITHUB_OUTPUT,
    Object.entries(outputs)
      .map(([name, value]) => `${name}=${value}\n`)
      .join(''),
  );
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;
if (isMain) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
