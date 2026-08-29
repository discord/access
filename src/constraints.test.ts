import {describe, expect, it} from 'vitest';

import {effectiveOwnerCantAddSelf, effectiveRequiredReason, effectiveTimeLimit} from './constraints';
import type {EffectiveConstraintDetail} from './api/apiSchemas';

// The API returns constraints already coalesced across whatever set was asked
// about, so these readers only look a value up. Anything resembling a min or
// an OR here would be the duplication the endpoint exists to remove.

function entry(constraint: string, value: number | boolean): EffectiveConstraintDetail {
  return {constraint, name: constraint, value, sources: []};
}

describe('effectiveTimeLimit', () => {
  it('reads the member limit for a membership', () => {
    expect(effectiveTimeLimit([entry('member_time_limit', 86400)], false)).toBe(86400);
  });

  it('reads the owner limit for an ownership', () => {
    expect(effectiveTimeLimit([entry('owner_time_limit', 3600)], true)).toBe(3600);
  });

  it('does not read the owner limit for a membership, or the reverse', () => {
    expect(effectiveTimeLimit([entry('owner_time_limit', 3600)], false)).toBeNull();
    expect(effectiveTimeLimit([entry('member_time_limit', 3600)], true)).toBeNull();
  });

  it('returns null when no limit applies', () => {
    expect(effectiveTimeLimit([], false)).toBeNull();
    expect(effectiveTimeLimit(undefined, false)).toBeNull();
  });

  it('returns a zero limit rather than treating it as absent', () => {
    // A falsy-but-present value is a real constraint; `?? null` on a truthiness
    // check would silently drop it and offer an unbounded duration.
    expect(effectiveTimeLimit([entry('member_time_limit', 0)], false)).toBe(0);
  });
});

describe('effectiveRequiredReason', () => {
  it('reads the member and owner keys by side', () => {
    expect(effectiveRequiredReason([entry('require_member_reason', true)], false)).toBe(true);
    expect(effectiveRequiredReason([entry('require_owner_reason', true)], true)).toBe(true);
    expect(effectiveRequiredReason([entry('require_member_reason', true)], true)).toBe(false);
  });

  it('is false when absent', () => {
    expect(effectiveRequiredReason([], false)).toBe(false);
    expect(effectiveRequiredReason(undefined, true)).toBe(false);
  });
});

describe('effectiveOwnerCantAddSelf', () => {
  it('reads the membership and ownership keys by side', () => {
    expect(effectiveOwnerCantAddSelf([entry('disallow_self_add_membership', true)], false)).toBe(true);
    expect(effectiveOwnerCantAddSelf([entry('disallow_self_add_ownership', true)], true)).toBe(true);
    expect(effectiveOwnerCantAddSelf([entry('disallow_self_add_membership', true)], true)).toBe(false);
  });

  it('is false when absent', () => {
    expect(effectiveOwnerCantAddSelf([], false)).toBe(false);
    expect(effectiveOwnerCantAddSelf(undefined, true)).toBe(false);
  });
});
