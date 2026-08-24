import dayjs, {Dayjs} from 'dayjs';

import {
  OktaUserDetail,
  OktaUserSummary,
  TagSummary,
  OktaGroupTagMapDetail,
  OktaUserGroupMemberDetail,
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
  if (!!displayName) {
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

export function getActiveTagsFromGroups(groups: GroupWithTags[]) {
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
      return out || curr.constraints![targetTag];
    } else return out;
  }, false);
}

export function minTagTime(tags: TagSummary[], owner: boolean) {
  if (owner) {
    const timeLimited = tags.filter(
      (tag: TagSummary) => tag.enabled && tag.constraints && Object.keys(tag.constraints).includes('owner_time_limit'),
    );
    return timeLimited.length > 0
      ? timeLimited.reduce((prev, curr) => {
          return prev < curr.constraints!['owner_time_limit'] ? prev : curr.constraints!['owner_time_limit'];
        }, Number.MAX_VALUE)
      : null;
  } else {
    const timeLimited = tags.filter(
      (tag: TagSummary) => tag.enabled && tag.constraints && Object.keys(tag.constraints).includes('member_time_limit'),
    );
    return timeLimited.length > 0
      ? timeLimited.reduce((prev, curr) => {
          return prev < curr.constraints!['member_time_limit'] ? prev : curr.constraints!['member_time_limit'];
        }, Number.MAX_VALUE)
      : null;
  }
}

export function minTagTimeGroups(groups: GroupWithTags[], owner: boolean) {
  return minTagTime(getActiveTagsFromGroups(groups), owner);
}

export function requiredReason(tags: TagSummary[] | undefined, owner: boolean) {
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
  [aUserId, aUsers]: [string, Array<OktaUserGroupMemberDetail>],
  [bUserId, bUsers]: [string, Array<OktaUserGroupMemberDetail>],
): number {
  let aEmail = aUsers[0].active_user?.email ?? '';
  let bEmail = bUsers[0].active_user?.email ?? '';
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

/**
 * The resolution_reason the backend writes when the sync cronjob closes a
 * request for age or for a lapsed window. Mirrors `EXPIRED_REQUEST_REASON` in
 * `api/syncer.py`, which is the producer.
 *
 * Expiration is deliberately not modelled as its own status or column, so this
 * string plus a null resolver is how the UI recognizes it. Keep the two in
 * sync; `tests/test_expired_reason_constant.py` fails if they drift.
 */
export const EXPIRED_REQUEST_REASON = 'Closed because the request expired';

export interface ExpirableRequest {
  status?: string | null;
  resolver?: {id?: string} | null;
  resolution_reason?: string | null;
}

/**
 * True when a request was closed by the expiration sweep rather than by a
 * person or by another system path.
 *
 * A null resolver alone is not enough: user deletion, group deletion, group
 * unmanaging, and a conditional-access plugin denial all close requests with no
 * resolver too. The reason string is what separates expiration from those, and
 * in particular from a policy denial, which must never be offered a one-click
 * reopen.
 *
 * Checks `resolver`, NOT `resolver_user_id`: AccessRequestDetail and
 * RoleRequestDetail expose both, but GroupRequestDetail exposes only `resolver`,
 * so keying off the id would silently never match on group requests.
 */
export function isExpiredRequest(request: ExpirableRequest): boolean {
  return (
    request.status === 'REJECTED' &&
    (request.resolver ?? null) === null &&
    request.resolution_reason === EXPIRED_REQUEST_REASON
  );
}

export interface ReconstructedUntil {
  /** An option id from `untilLabels`, or 'indefinite', or 'custom'. */
  until: string;
  /**
   * Set whenever `until` is 'custom' — including the clamped case, where it
   * is a valid date set to exactly the tag's `timeLimit` from now, so a form
   * fed this result always has something to show in the date picker. A
   * Dayjs, because that is what DatePickerElement consumes.
   */
  customUntil?: Dayjs;
  /**
   * The raw rounded duration in seconds, or null when there was no end date.
   * Always unclamped, even when `until` was clamped by `timeLimit`. Exposed so
   * callers that need the delta for their own logic (the read views' separate
   * tag-limit clamp) do not recompute it.
   */
  deltaSeconds: number | null;
}

/**
 * The clamped result for a tag `timeLimit`: the largest numeric option that
 * fits, or a custom date at exactly the limit when no option is small enough.
 */
function clampedToTimeLimit(
  untilLabels: Record<string, string>,
  timeLimit: number,
  deltaSeconds: number | null,
): ReconstructedUntil {
  const allowed = Object.keys(untilLabels)
    .filter((key) => !isNaN(Number(key)) && Number(key) <= timeLimit)
    .sort((a, b) => Number(a) - Number(b));
  const largest = allowed.at(-1);
  if (largest != null) {
    return {until: largest, deltaSeconds};
  }
  // No option fits the limit; offer a custom date at exactly the limit rather
  // than a 'custom' selection with nothing in the picker.
  return {until: 'custom', customUntil: dayjs().add(timeLimit, 'second'), deltaSeconds};
}

/**
 * Recover the duration a request originally asked for, re-based from today.
 *
 * Requests store an absolute `request_ending_at`, so a request that expired
 * because its window lapsed has a stored date in the past that cannot be
 * re-submitted as-is. Diffing against `created_at` recovers the *duration* the
 * requester picked: an exact match in `untilLabels` round-trips to that option,
 * and anything else was a custom date, so it is re-offered as one based from
 * now.
 *
 * `timeLimit` (seconds, from a tag constraint) is OPTIONAL and defaults to no
 * clamping. Pass it only where you want the result restricted to what the tag
 * currently allows. The two Read.tsx call sites deliberately pass nothing,
 * because they apply their own clamp separately when building form
 * defaultValues; passing it there would double-clamp.
 */
export function reconstructRequestedUntil(args: {
  createdAt?: string | null;
  endingAt?: string | null;
  untilLabels: Record<string, string>;
  timeLimit?: number | null;
}): ReconstructedUntil {
  const {createdAt, endingAt, untilLabels, timeLimit} = args;

  if (endingAt == null) {
    // A tag time limit forbids indefinite access, so clamp rather than offer it.
    if (timeLimit != null) {
      return clampedToTimeLimit(untilLabels, timeLimit, null);
    }
    return {until: 'indefinite', deltaSeconds: null};
  }

  // Round to the nearest 100s to absorb the sub-second drift between the form
  // computing the date and the row being written.
  const deltaSeconds = Math.round(dayjs(endingAt).diff(dayjs(createdAt), 'second') / 100) * 100;

  if (timeLimit != null && deltaSeconds > timeLimit) {
    return clampedToTimeLimit(untilLabels, timeLimit, deltaSeconds);
  }

  if (deltaSeconds.toString() in untilLabels) {
    return {until: deltaSeconds.toString(), deltaSeconds};
  }

  return {until: 'custom', customUntil: dayjs().add(deltaSeconds, 'second'), deltaSeconds};
}
