import {GroupRefForMembership, OktaGroupTagMapDetail, TagPropagationTargetDetail} from '../../api/apiSchemas';

// The "Groups with Tag" table used to be row-per-tag-map: a group tagged both
// directly and via its app produced two rows for the same group, and a role
// reached only by propagation through an associated group never appeared at
// all (propagation isn't stored as an `OktaGroupTagMap` row). This module
// merges `active_group_tags` (direct + app-inherited) and `propagated_to_groups`
// (role reached via an associated group) into one row per entity, each
// carrying every source that applies -- see groupsWithTagRows.test.ts.

export type TagSourceChip =
  | {kind: 'direct'}
  | {kind: 'app'; appName: string}
  | {kind: 'group'; sourceGroupName: string};

export interface GroupWithTagRow {
  groupId: string;
  groupName: string;
  groupType: string;
  chips: TagSourceChip[];
  // Only set when `chips` contains a 'direct' entry -- the group ref needed
  // to end that direct assignment. App-inherited and propagated sources are
  // removed elsewhere (removing the app tag, or ending the association), not
  // from this table, so they carry no group ref here.
  directGroup?: GroupRefForMembership;
}

export function buildGroupsWithTagRows(
  activeGroupTags: OktaGroupTagMapDetail[] | undefined | null,
  propagatedToGroups: TagPropagationTargetDetail[] | undefined | null,
): GroupWithTagRow[] {
  const rowsById = new Map<string, GroupWithTagRow>();

  const rowFor = (groupId: string, groupName: string, groupType: string): GroupWithTagRow => {
    const existing = rowsById.get(groupId);
    if (existing) {
      return existing;
    }
    const created: GroupWithTagRow = {groupId, groupName, groupType, chips: []};
    rowsById.set(groupId, created);
    return created;
  };

  (activeGroupTags ?? []).forEach((tagMap) => {
    const group = tagMap.active_group;
    if (!group?.id) {
      return;
    }
    const row = rowFor(group.id, group.name, group.type);
    if (tagMap.active_app_tag_mapping) {
      row.chips.push({kind: 'app', appName: group.app?.name ?? ''});
    } else {
      row.chips.push({kind: 'direct'});
      row.directGroup = group;
    }
  });

  (propagatedToGroups ?? []).forEach((entry) => {
    const row = rowFor(entry.group_id, entry.group_name, entry.group_type);
    row.chips.push({kind: 'group', sourceGroupName: entry.source_group_name});
  });

  return Array.from(rowsById.values());
}

export function chipLabel(chip: TagSourceChip): string {
  switch (chip.kind) {
    case 'direct':
      return 'Direct';
    case 'app':
      return `App: ${chip.appName}`;
    case 'group':
      return `Group: ${chip.sourceGroupName}`;
  }
}

export function hasDirectChip(row: GroupWithTagRow): boolean {
  return row.chips.some((chip) => chip.kind === 'direct');
}
