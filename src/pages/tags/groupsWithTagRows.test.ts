import {describe, expect, it} from 'vitest';

import {GroupRefForMembership, OktaGroupTagMapDetail, TagPropagationTargetDetail} from '../../api/apiSchemas';
import {buildGroupsWithTagRows, chipLabel, hasDirectChip} from './groupsWithTagRows';

const group = (overrides: Partial<GroupRefForMembership> = {}): GroupRefForMembership => ({
  id: 'g1',
  type: 'okta_group',
  name: 'group-one',
  ...overrides,
});

const directTagMap = (overrides: Partial<OktaGroupTagMapDetail> = {}): OktaGroupTagMapDetail => ({
  created_at: null,
  active_group: group(),
  ...overrides,
});

const propagationEntry = (overrides: Partial<TagPropagationTargetDetail> = {}): TagPropagationTargetDetail => ({
  group_id: 'r1',
  group_name: 'Role-Foo',
  group_type: 'role_group',
  source_group_id: 'g1',
  source_group_name: 'group-one',
  origin: 'member_association',
  ...overrides,
});

describe('buildGroupsWithTagRows', () => {
  it('returns one row per direct group tag with a direct chip and the group ref', () => {
    const rows = buildGroupsWithTagRows([directTagMap()], []);
    expect(rows).toHaveLength(1);
    expect(rows[0].groupId).toBe('g1');
    expect(rows[0].chips).toEqual([{kind: 'direct'}]);
    expect(rows[0].directGroup?.id).toBe('g1');
  });

  it('renders an app-inherited tag map as an app chip, not direct', () => {
    const appGroup = group({id: 'ag1', type: 'app_group', app: {id: 'app1', name: 'Foo'}});
    const rows = buildGroupsWithTagRows(
      [directTagMap({active_group: appGroup, active_app_tag_mapping: {id: 1} as any})],
      [],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].chips).toEqual([{kind: 'app', appName: 'Foo'}]);
    expect(rows[0].directGroup).toBeUndefined();
  });

  it('lists a role reached only by propagation with a group chip and no direct group ref', () => {
    const rows = buildGroupsWithTagRows([], [propagationEntry()]);
    expect(rows).toHaveLength(1);
    expect(rows[0].groupId).toBe('r1');
    expect(rows[0].groupName).toBe('Role-Foo');
    expect(rows[0].groupType).toBe('role_group');
    expect(rows[0].chips).toEqual([{kind: 'group', sourceGroupName: 'group-one'}]);
    expect(rows[0].directGroup).toBeUndefined();
  });

  it('merges a group appearing in both a direct tag map and propagation into one row with two chips', () => {
    // A role can be directly tagged AND reached by propagation through a
    // different tagged group it belongs to -- both sources should surface
    // on the same row, not two separate rows for the same entity.
    const roleGroup = group({id: 'r1', type: 'role_group', name: 'Role-Foo'});
    const rows = buildGroupsWithTagRows(
      [directTagMap({active_group: roleGroup})],
      [propagationEntry({group_id: 'r1', group_name: 'Role-Foo'})],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].chips).toEqual([{kind: 'direct'}, {kind: 'group', sourceGroupName: 'group-one'}]);
    expect(rows[0].directGroup?.id).toBe('r1');
  });

  it('keeps a role reached via two groups on one row, with a chip per source group', () => {
    const rows = buildGroupsWithTagRows(
      [],
      [
        propagationEntry({source_group_id: 'g1', source_group_name: 'group-one'}),
        propagationEntry({source_group_id: 'g2', source_group_name: 'group-two'}),
      ],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].chips).toEqual([
      {kind: 'group', sourceGroupName: 'group-one'},
      {kind: 'group', sourceGroupName: 'group-two'},
    ]);
  });

  it('returns an empty list when both inputs are empty or missing', () => {
    expect(buildGroupsWithTagRows([], [])).toEqual([]);
    expect(buildGroupsWithTagRows(undefined, undefined)).toEqual([]);
    expect(buildGroupsWithTagRows(null, null)).toEqual([]);
  });

  it('skips a tag map whose active_group is missing', () => {
    const rows = buildGroupsWithTagRows([directTagMap({active_group: undefined})], []);
    expect(rows).toEqual([]);
  });
});

describe('chipLabel', () => {
  it('labels a direct chip', () => {
    expect(chipLabel({kind: 'direct'})).toBe('Direct');
  });

  it('labels an app chip with the app name', () => {
    expect(chipLabel({kind: 'app', appName: 'Foo'})).toBe('App: Foo');
  });

  it('labels a group chip with the source group name', () => {
    expect(chipLabel({kind: 'group', sourceGroupName: 'group-one'})).toBe('Group: group-one');
  });
});

describe('hasDirectChip', () => {
  it('is true when a row has a direct chip among others', () => {
    const row = {
      groupId: 'g1',
      groupName: 'g',
      groupType: 'okta_group',
      chips: [{kind: 'group' as const, sourceGroupName: 's'}, {kind: 'direct' as const}],
    };
    expect(hasDirectChip(row)).toBe(true);
  });

  it('is false when a row has no direct chip', () => {
    const row = {
      groupId: 'g1',
      groupName: 'g',
      groupType: 'okta_group',
      chips: [{kind: 'app' as const, appName: 'Foo'}],
    };
    expect(hasDirectChip(row)).toBe(false);
  });
});
