// The base line of an app owner group's description. The backend owns this format in
// `app_owners_group_description` (api/models/app_group.py) and validates against it; the
// form needs it here to split the stored description into an immutable prefix and an
// editable remainder. `test_frontend_prefix_template_matches_backend`
// (tests/test_app_group_description.py) enforces that this template literal agrees with
// the backend's format string -- if you change the wording, change it in both places.
//
// This mirrors how the group name is handled in this directory: APP_GROUP_PREFIX and
// ROLE_GROUP_PREFIX in CreateUpdate.tsx are the same kind of client-side copy.
export function appOwnerGroupDescriptionPrefix(appName: string): string {
  return `Owners of the ${appName} application`;
}

// The blank line composeAppOwnerGroupDescription joins the base line and remainder with.
const BASE_LINE_SEPARATOR = '\n\n';

const LEADING_BLANK_LINES_RE = /^(?:[ \t]*\n)+/;

/**
 * Strip leading blank lines and trailing whitespace, preserving indentation on the first
 * line of real content.
 *
 * Leading spaces/tabs on a non-blank line are meaningful markdown (an indented list item
 * or code block), so unlike `String.trim()` this does not treat a run of leading
 * whitespace and newlines as one unit to discard; only whole blank lines ahead of the
 * first real content are removed. Trailing whitespace carries no such meaning and is
 * stripped in full. Mirrors `_trim_free_text` in `api/models/app_group.py`.
 *
 * `\r\n` is normalized to `\n` first, since a browser textarea submits CRLF line endings.
 */
function trimFreeText(text: string): string {
  return text.replace(/\r\n/g, '\n').replace(LEADING_BLANK_LINES_RE, '').trimEnd();
}

/**
 * The free text below an app owner group's base line.
 *
 * A description starting with the expected base line splits into base + remainder; one
 * that does not becomes the remainder whole, so text written before the group conformed
 * stays visible and editable rather than being hidden behind a prefix that does not
 * match it. Mirrors `app_owners_group_description_remainder` on the backend.
 */
export function appOwnerGroupDescriptionRemainder(description: string, appName: string): string {
  const normalized = (description ?? '').replace(/\r\n/g, '\n');
  const prefix = appOwnerGroupDescriptionPrefix(appName);

  if (normalized.trim() === prefix) {
    return '';
  }
  if (normalized.startsWith(prefix)) {
    return trimFreeText(normalized.slice(prefix.length));
  }
  return trimFreeText(normalized);
}

/** The base line, plus a blank line and `remainder` when there is any. */
export function composeAppOwnerGroupDescription(appName: string, remainder: string): string {
  const prefix = appOwnerGroupDescriptionPrefix(appName);
  const trimmed = trimFreeText(remainder ?? '');
  return trimmed.length > 0 ? `${prefix}${BASE_LINE_SEPARATOR}${trimmed}` : prefix;
}

/**
 * The longest `remainder` composeAppOwnerGroupDescription can accept for `appName` without the
 * composed description exceeding the backend's 1024-character description limit.
 *
 * Derived from the base line's length and the separator composeAppOwnerGroupDescription joins
 * with, rather than a hardcoded figure, so a change to either does not silently open a gap
 * between this cap and the limit it exists to enforce. Floored at zero, which is unreachable in
 * practice since `App.name` is capped at 255 characters.
 */
export function appOwnerGroupDescriptionRemainderMaxLength(appName: string): number {
  return Math.max(0, 1024 - appOwnerGroupDescriptionPrefix(appName).length - BASE_LINE_SEPARATOR.length);
}
