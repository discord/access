import {PolymorphicGroup, OktaUser, Tag, OktaGroupTagMap, OktaUserGroupMember} from './api/apiSchemas';

export const perPage: Array<number | {label: string; value: number}> = [5, 10, 20, 50, {label: 'All', value: -1}];

export function displayGroupType(group: PolymorphicGroup | undefined) {
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

export function displayUserName(user: OktaUser | undefined) {
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

export function getActiveTagsFromGroups(groups: PolymorphicGroup[]) {
  return Array.from(
    groups.reduce((allTags, curr) => {
      if (curr.active_group_tags) {
        const groupTags = curr.active_group_tags.map((t: OktaGroupTagMap) => t.active_tag!);
        groupTags.forEach((item) => allTags.add(item));
        return allTags;
      } else return allTags;
    }, new Set<Tag>()),
  );
}

// returns true if targetTag is set to true at least once in the tag list
function checkBooleanTag(tags: Tag[] | undefined, targetTag: string) {
  if (!tags) return false;

  return tags.reduce((out: boolean, curr: Tag) => {
    if (curr.enabled && curr.constraints && Object.keys(curr.constraints).includes(targetTag)) {
      return out || curr.constraints![targetTag];
    } else return out;
  }, false);
}

export function minTagTime(tags: Tag[], owner: boolean) {
  if (owner) {
    const timeLimited = tags.filter(
      (tag: Tag) => tag.enabled && tag.constraints && Object.keys(tag.constraints).includes('owner_time_limit'),
    );
    return timeLimited.length > 0
      ? timeLimited.reduce((prev, curr) => {
          return prev < curr.constraints!['owner_time_limit'] ? prev : curr.constraints!['owner_time_limit'];
        }, Number.MAX_VALUE)
      : null;
  } else {
    const timeLimited = tags.filter(
      (tag: Tag) => tag.enabled && tag.constraints && Object.keys(tag.constraints).includes('member_time_limit'),
    );
    return timeLimited.length > 0
      ? timeLimited.reduce((prev, curr) => {
          return prev < curr.constraints!['member_time_limit'] ? prev : curr.constraints!['member_time_limit'];
        }, Number.MAX_VALUE)
      : null;
  }
}

export function minTagTimeGroups(groups: PolymorphicGroup[], owner: boolean) {
  return minTagTime(getActiveTagsFromGroups(groups), owner);
}

export function requiredReason(tags: Tag[] | undefined, owner: boolean) {
  if (!tags) return false;

  return owner ? checkBooleanTag(tags, 'require_owner_reason') : checkBooleanTag(tags, 'require_member_reason');
}

export function requiredReasonGroups(groups: PolymorphicGroup[], owner: boolean) {
  return requiredReason(getActiveTagsFromGroups(groups), owner);
}

export function ownerCantAddSelf(tags: Tag[] | undefined, owner: boolean) {
  if (!tags) return false;

  return owner
    ? checkBooleanTag(tags, 'disallow_self_add_ownership')
    : checkBooleanTag(tags, 'disallow_self_add_membership');
}

export function ownerCantAddSelfGroups(groups: PolymorphicGroup[], owner: boolean) {
  return ownerCantAddSelf(getActiveTagsFromGroups(groups), owner);
}

export function sortGroupMembers(
  [aUserId, aUsers]: [string, Array<OktaUserGroupMember>],
  [bUserId, bUsers]: [string, Array<OktaUserGroupMember>],
): number {
  let aEmail = aUsers[0].active_user?.email ?? '';
  let bEmail = bUsers[0].active_user?.email ?? '';
  return aEmail.localeCompare(bEmail);
}

export function sortGroupMemberRecords(users: Record<string, OktaUser>): OktaUser[] {
  const usersArray = Object.values(users); // Convert the object to an array
  usersArray.sort((a, b) => {
    const nameA = `${a.first_name} ${a.last_name}`;
    const nameB = `${b.first_name} ${b.last_name}`;
    return nameA.localeCompare(nameB);
  });
  return usersArray;
}

export function groupMemberships(
  memberships: Array<OktaUserGroupMember> | undefined,
): Record<string, Array<OktaUserGroupMember>> {
  return groupBy(memberships ?? [], (membership) => membership.active_user?.id ?? '');
}
