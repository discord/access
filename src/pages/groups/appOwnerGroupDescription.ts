// The base line of an app owner group's description. The backend owns this format in
// `app_owners_group_description` (api/models/app_group.py) and validates against it; the
// form needs it here to split the stored description into an immutable prefix and an
// editable remainder. Both copies are pinned by tests that name each other -- if you
// change the wording, change it in both places.
//
// This mirrors how the group name is handled in this directory: APP_GROUP_PREFIX and
// ROLE_GROUP_PREFIX in CreateUpdate.tsx are the same kind of client-side copy.
export function appOwnerGroupDescriptionPrefix(appName: string): string {
  return `Owners of the ${appName} application`;
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
    return normalized.slice(prefix.length).trim();
  }
  return normalized.trim();
}

/** The base line, plus a blank line and `remainder` when there is any. */
export function composeAppOwnerGroupDescription(appName: string, remainder: string): string {
  const prefix = appOwnerGroupDescriptionPrefix(appName);
  const trimmed = (remainder ?? '').trim();
  return trimmed.length > 0 ? `${prefix}\n\n${trimmed}` : prefix;
}
