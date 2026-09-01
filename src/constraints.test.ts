import {describe, expect, it} from 'vitest';

import {
  effectiveOwnerCantAddSelf,
  effectiveRequiredReason,
  effectiveTimeLimit,
  settledConstraints,
} from './constraints';
import type {EffectiveConstraintDetail} from './api/apiSchemas';

// The API returns constraints already coalesced across whatever set was asked
// about, so these readers only look a value up. Anything resembling a min or
// an OR over tags here would be the duplication the endpoint exists to remove.

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

describe('settledConstraints', () => {
  it('is never blocked, so it reports exactly what it was given', () => {
    const reader = settledConstraints([entry('member_time_limit', 86400)]);
    expect(reader.pending).toBe(false);
    expect(reader.error).toBeNull();
    expect(reader.blocked).toBe(false);
    expect(reader.timeLimit(false)).toBe(86400);
    expect(reader.requiresReason(false)).toBe(false);
    expect(reader.cantAddSelf(false)).toBe(false);
  });

  it('reports nothing applying for a group with no constraints', () => {
    const reader = settledConstraints([]);
    expect(reader.timeLimit(true)).toBeNull();
    expect(reader.requiresReason(true)).toBe(false);
    expect(reader.cantAddSelf(true)).toBe(false);
  });

  it('answers per group only for a group it holds, which it never does', () => {
    // `settledConstraints` wraps one group's own constraints, so there is no
    // per-group map to consult; asking for a row falls through to unknown-but-
    // -settled, which reports nothing applying rather than failing closed.
    const reader = settledConstraints([entry('disallow_self_add_membership', true)]);
    expect(reader.cantAddSelf(false)).toBe(true);
    expect(reader.forGroup('some-other-group').cantAddSelf(false)).toBe(false);
  });
});
