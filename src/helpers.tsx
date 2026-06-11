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
