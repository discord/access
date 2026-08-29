import * as React from 'react';

import {skipToken} from '@tanstack/react-query';

import {useEffectiveConstraints} from './api/apiComponents';
import type {EffectiveConstraintDetail} from './api/apiSchemas';

// Reading the constraints that apply to a group, a set of groups, or a set of
// tags.
//
// The coalescing itself — which tags are enabled, which reach a role through
// its associations, and the minimum or logical OR across them — lives in
// `api/models/tag.py` and is served by `GET /api/constraints/effective`. These
// readers only look a value up in what the API already resolved. Anything here
// resembling a min or an OR would be re-introducing the duplication that
// endpoint exists to remove.

const TIME_LIMIT_KEYS = {member: 'member_time_limit', owner: 'owner_time_limit'} as const;
const REQUIRE_REASON_KEYS = {member: 'require_member_reason', owner: 'require_owner_reason'} as const;
const SELF_ADD_KEYS = {member: 'disallow_self_add_membership', owner: 'disallow_self_add_ownership'} as const;

type Constraints = EffectiveConstraintDetail[] | undefined | null;

function valueOf(constraints: Constraints, key: string): number | boolean | undefined {
  return constraints?.find((entry) => entry.constraint === key)?.value;
}

function side<T>(keys: {member: T; owner: T}, owner: boolean): T {
  return owner ? keys.owner : keys.member;
}

// Seconds, or null when no limit applies. Compared against `undefined` rather
// than tested for truthiness: a `0`-second limit is a real constraint, and
// treating it as absent would offer an unbounded duration.
export function effectiveTimeLimit(constraints: Constraints, owner: boolean): number | null {
  const value = valueOf(constraints, side(TIME_LIMIT_KEYS, owner));
  return typeof value === 'number' ? value : null;
}

export function effectiveRequiredReason(constraints: Constraints, owner: boolean): boolean {
  return valueOf(constraints, side(REQUIRE_REASON_KEYS, owner)) === true;
}

export function effectiveOwnerCantAddSelf(constraints: Constraints, owner: boolean): boolean {
  return valueOf(constraints, side(SELF_ADD_KEYS, owner)) === true;
}

// Stable, deduplicated, sorted ids so the query key does not change when the
// same selection arrives in a different order. That makes the result cacheable
// and, in the bulk dialogs where the selection changes as rows are toggled,
// keeps a slow response for an older selection from landing after a newer one.
function stableIds(ids: (string | undefined | null)[]): string[] {
  return Array.from(new Set(ids.filter((id): id is string => !!id))).sort();
}

// The constraints in force across `groupIds` taken together, plus each group's
// own. Skips the request entirely when nothing is selected.
export function useConstraintsForGroups(groupIds: (string | undefined | null)[]) {
  const ids = React.useMemo(() => stableIds(groupIds), [groupIds.join(',')]);
  return useEffectiveConstraints(ids.length > 0 ? {queryParams: {group_ids: ids}} : skipToken);
}

// The constraints a set of tags would impose, for the case where no group
// exists yet to ask about — approving a group request.
export function useConstraintsForTags(tagIds: (string | undefined | null)[]) {
  const ids = React.useMemo(() => stableIds(tagIds), [tagIds.join(',')]);
  return useEffectiveConstraints(ids.length > 0 ? {queryParams: {tag_ids: ids}} : skipToken);
}
