import {
  OktaUserDetail,
  OktaUserSummary,
  TagSummary,
  OktaGroupTagMapDetail,
  OktaUserGroupMemberDetail,
  ProblemDetail,
} from './api/apiSchemas';

// The nested group shapes the helpers below operate on all carry a `type`
// discriminator and an `active_group_tags` list, but appear in several
// generated variants (GroupDetail, GroupSummary, GroupRef, GroupRefForMembership).
// These structural aliases accept any of them.
type GroupTypeLike = {type?: string | null};
// `id` is required (and present on every group variant) so this isn't a "weak
// type" — that lets the slimmer `GroupRefForMembership` (which carries no
// `active_group_tags`) be passed too; it simply contributes no tags.
type GroupWithTags = {id: string; active_group_tags?: OktaGroupTagMapDetail[] | null};

/**
 * The message to show for a failed API call.
 *
 * The fetcher flattens every error response to a string `payload`, but the
 * generated `*Error` types also admit the raw problem-detail body, so this
 * reads `detail` (then `title`) when handed one.
 *
 * @param error - the `error` a generated query or mutation hook reports.
 * @returns the human-readable message.
 */
export function requestErrorMessage(error: {payload: string | ProblemDetail}): string {
  const {payload} = error;
  return typeof payload === 'string' ? payload : (payload.detail ?? payload.title ?? 'Unexpected error');
}

/**
 * The configuration and status an app-group-lifecycle plugin keeps for one
 * app, group, or group request, stored under the plugin's id in `plugin_data`.
 */
export type PluginDataEntry = {
  configuration?: Record<string, unknown>;
  status?: Record<string, unknown>;
};

/**
 * One plugin's entry in a `plugin_data` map.
 *
 * The generated client types `plugin_data` as an open map of `any`; this
 * gives the entry the shape the backend writes.
 *
 * @param pluginData - an app's, group's, or group request's `plugin_data`.
 * @param pluginId - the app-group-lifecycle plugin's id.
 * @returns the plugin's entry, or `undefined` when there is no data or no plugin id.
 */
export function pluginDataEntry(
  pluginData: Record<string, unknown> | null | undefined,
  pluginId: string | null | undefined,
): PluginDataEntry | undefined {
  if (pluginData == null || !pluginId) {
    return undefined;
  }
  return pluginData[pluginId] as PluginDataEntry | undefined;
}

/**
 * Display text for a value from an app-group-lifecycle plugin's configuration or status data.
 *
 * Plugins declare text, number, boolean, and date properties, so values are
 * normally primitives and render as `String` would. A value outside that
 * schema is shown as JSON rather than `[object Object]`.
 *
 * @param value - the stored value; callers handle `null` and `undefined` themselves.
 * @returns the text to render.
 */
export function formatPluginValue(value: unknown): string {
  switch (typeof value) {
    case 'string':
    case 'number':
    case 'boolean':
    case 'bigint':
    case 'symbol':
    case 'undefined':
      return String(value);
    default:
      return JSON.stringify(value);
  }
}

export const perPage: number[] = [5, 10, 20, 50, 200, 1000];

// Number of blank filler rows used to pad a paginated table to a consistent
// height, avoiding a layout jump when paging to a shorter last page. Only past
// the first page (the first/only page has no earlier height to match) and only
// for the smaller page sizes — padding a 200/1000-row page would leave a huge
// empty gap below the data.
export function emptyTableRows(page: number, rowsPerPage: number, rowCount: number): number {
  return page > 0 && rowsPerPage <= 50 ? rowsPerPage - rowCount : 0;
}

export function displayGroupType(group: GroupTypeLike | null | undefined) {
  if (group == undefined || group.type == undefined) {
    return '';
  }
  if (group.type == 'okta_group') {
    return 'Group';
  }
  if (group.type == 'role_group') {
    return 'Role';
  }
  return group.type
    .split('_')
    .map((word) => word[0].toUpperCase() + word.substring(1))
    .join(' ');
}

export function displayUserName(user: OktaUserSummary | null | undefined) {
  if (user == undefined) {
    return '';
  }
  return user.display_name != null ? user.display_name : user.first_name + ' ' + user.last_name;
}

export function extractEmailFromDisplayName(displayName: string | null) {
  if (displayName) {
    const emailMatch = displayName.match(/\(([^)]+)\)/);
    return emailMatch ? emailMatch[1].toLowerCase() : '';
  }
  return '';
}

// https://stackoverflow.com/a/34890276
export function groupBy<T>(xs: T[] | undefined, keyFn: (item: T) => string | undefined) {
  return (xs ?? []).reduce(
    (rv, x) => {
      const newKey = keyFn(x) ?? '';
      (rv[newKey] = rv[newKey] || []).push(x);
      return rv;
    },
    {} as Record<string, T[]>,
  );
}

// Constraint helpers reading a group's OWN tags — direct and app-inherited —
// and nothing else. They do not know about propagation to roles, and they must
// not be handed a role's associated groups to simulate it: that walk lived in
// several dialogs, drifted from the backend, and is now served by
// `GET /api/constraints/effective` (see `src/constraints.ts`).
//
// What is left is the one direction where no propagation exists: attaching a
// role to a group, where the constraints that apply are the target group's and
// the target is never itself a role. `roles/AddGroups` also asks the question
// per option across a live search, which is unbounded and so not a good fit
// for a request per keystroke.
function getActiveTagsFromGroups(groups: GroupWithTags[]) {
  return Array.from(
    groups.reduce((allTags, curr) => {
      if (curr.active_group_tags) {
        const groupTags = curr.active_group_tags.map((t: OktaGroupTagMapDetail) => t.active_tag!);
        groupTags.forEach((item) => allTags.add(item));
        return allTags;
      } else return allTags;
    }, new Set<TagSummary>()),
  );
}

// returns true if targetTag is set to true at least once in the tag list
function checkBooleanTag(tags: TagSummary[] | undefined, targetTag: string) {
  if (!tags) return false;

  return tags.reduce((out: boolean, curr: TagSummary) => {
    if (curr.enabled && curr.constraints && Object.keys(curr.constraints).includes(targetTag)) {
      return out || (curr.constraints[targetTag] as boolean);
    } else return out;
  }, false);
}

function minTagTime(tags: TagSummary[], owner: boolean) {
  if (owner) {
    const timeLimited = tags.filter(
      (tag: TagSummary) => tag.enabled && tag.constraints && Object.keys(tag.constraints).includes('owner_time_limit'),
    );
    return timeLimited.length > 0
      ? timeLimited.reduce((prev, curr) => {
          const limit = curr.constraints!['owner_time_limit'] as number;
          return prev < limit ? prev : limit;
        }, Number.MAX_VALUE)
      : null;
  } else {
    const timeLimited = tags.filter(
      (tag: TagSummary) => tag.enabled && tag.constraints && Object.keys(tag.constraints).includes('member_time_limit'),
    );
    return timeLimited.length > 0
      ? timeLimited.reduce((prev, curr) => {
          const limit = curr.constraints!['member_time_limit'] as number;
          return prev < limit ? prev : limit;
        }, Number.MAX_VALUE)
      : null;
  }
}

export function minTagTimeGroups(groups: GroupWithTags[], owner: boolean) {
  return minTagTime(getActiveTagsFromGroups(groups), owner);
}

function requiredReason(tags: TagSummary[] | undefined, owner: boolean) {
  if (!tags) return false;

  return owner ? checkBooleanTag(tags, 'require_owner_reason') : checkBooleanTag(tags, 'require_member_reason');
}

export function requiredReasonGroups(groups: GroupWithTags[], owner: boolean) {
  return requiredReason(getActiveTagsFromGroups(groups), owner);
}

export function ownerCantAddSelf(tags: TagSummary[] | undefined, owner: boolean) {
  if (!tags) return false;

  return owner
    ? checkBooleanTag(tags, 'disallow_self_add_ownership')
    : checkBooleanTag(tags, 'disallow_self_add_membership');
}

export function ownerCantAddSelfGroups(groups: GroupWithTags[], owner: boolean) {
  return ownerCantAddSelf(getActiveTagsFromGroups(groups), owner);
}

export function sortGroupMembers(
  [, aUsers]: [string, Array<OktaUserGroupMemberDetail>],
  [, bUsers]: [string, Array<OktaUserGroupMemberDetail>],
): number {
  const aEmail = aUsers[0].active_user?.email ?? '';
  const bEmail = bUsers[0].active_user?.email ?? '';
  return aEmail.localeCompare(bEmail);
}

export function sortGroupMemberRecords(users: Record<string, OktaUserDetail>): OktaUserDetail[] {
  const usersArray = Object.values(users); // Convert the object to an array
  usersArray.sort((a, b) => {
    const nameA = `${a.first_name} ${a.last_name}`;
    const nameB = `${b.first_name} ${b.last_name}`;
    return nameA.localeCompare(nameB);
  });
  return usersArray;
}

export function groupMemberships(
  memberships: Array<OktaUserGroupMemberDetail> | undefined,
): Record<string, Array<OktaUserGroupMemberDetail>> {
  return groupBy(memberships ?? [], (membership) => membership.active_user?.id ?? '');
}
