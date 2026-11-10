// Detection of credential-shaped text in review output.
//
// The review runner and the publisher both refuse to emit a review that contains anything
// matching these patterns. The agent never holds a credential, so a match means either the
// model echoed something it should not have or the pull request itself contains a secret; in
// both cases the safe action is to publish nothing from the model.

/** Credential formats the gate refuses to publish, keyed by a name safe to log. */
export const SECRET_PATTERNS = new Map([
  ['anthropic-api-key', /sk-ant-[A-Za-z0-9_-]{16,}/u],
  ['openai-api-key', /sk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}/u],
  ['github-token', /\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,})/u],
  ['aws-access-key-id', /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/u],
  ['google-api-key', /\bAIza[0-9A-Za-z_-]{35}\b/u],
  ['slack-token', /\bxox[abprs]-[A-Za-z0-9-]{10,}/u],
  // The URL is the credential: anyone holding it can post to the channel.
  [
    'discord-webhook-url',
    /\bdiscord(?:app)?\.com\/api\/(?:v\d+\/)?webhooks\/\d{17,20}\/[A-Za-z0-9_-]{60,}/u,
  ],
  // Organization (sntrys_) and user (sntryu_) auth tokens. DSNs are not listed: Sentry treats
  // them as public client configuration.
  ['sentry-auth-token', /\bsntry[su]_[A-Za-z0-9+/=_]{40,}/u],
  ['private-key', /-----BEGIN [A-Z ]*PRIVATE KEY-----/u],
]);

// Long enough that a match is not a coincidence, short enough to catch a partial echo.
const EXACT_WINDOW = 16;

/**
 * Find credential-shaped text.
 *
 * @param {string} text - The text to scan, typically serialized review output.
 * @param {string[]} [exactSecrets] - Known secret values. Any 16-character slice of one that
 *   appears in `text` counts as a match, so a truncated or partial echo is caught too.
 * @returns {string[]} The names of the patterns that matched, never the matched text itself.
 *   Empty when the text is clean.
 */
export function findSecretLikeText(text, exactSecrets = []) {
  const value = String(text ?? '');
  const matches = [];
  for (const [name, pattern] of SECRET_PATTERNS) {
    if (pattern.test(value)) matches.push(name);
  }
  for (const secret of exactSecrets) {
    if (typeof secret !== 'string' || secret.length < EXACT_WINDOW) continue;
    for (let start = 0; start + EXACT_WINDOW <= secret.length; start += 1) {
      if (value.includes(secret.slice(start, start + EXACT_WINDOW))) {
        matches.push('configured-api-key');
        break;
      }
    }
  }
  return matches;
}
