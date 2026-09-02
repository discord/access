import {describe, expect, it} from 'vitest';

import {
  approvalUntilDefault,
  isSelfAddDisallowed,
  isReasonRequired,
  effectiveTimeLimit,
  carriedConstraints,
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

describe('isReasonRequired', () => {
  it('reads the member and owner keys by side', () => {
    expect(isReasonRequired([entry('require_member_reason', true)], false)).toBe(true);
    expect(isReasonRequired([entry('require_owner_reason', true)], true)).toBe(true);
    expect(isReasonRequired([entry('require_member_reason', true)], true)).toBe(false);
  });

  it('is false when absent', () => {
    expect(isReasonRequired([], false)).toBe(false);
    expect(isReasonRequired(undefined, true)).toBe(false);
  });
});

describe('isSelfAddDisallowed', () => {
  it('reads the membership and ownership keys by side', () => {
    expect(isSelfAddDisallowed([entry('disallow_self_add_membership', true)], false)).toBe(true);
    expect(isSelfAddDisallowed([entry('disallow_self_add_ownership', true)], true)).toBe(true);
    expect(isSelfAddDisallowed([entry('disallow_self_add_membership', true)], true)).toBe(false);
  });

  it('is false when absent', () => {
    expect(isSelfAddDisallowed([], false)).toBe(false);
    expect(isSelfAddDisallowed(undefined, true)).toBe(false);
  });
});

describe('carriedConstraints', () => {
  it('is never blocked, so it reports exactly what it was given', () => {
    const reader = carriedConstraints([entry('member_time_limit', 86400)]);
    expect(reader.pending).toBe(false);
    expect(reader.error).toBeNull();
    expect(reader.blocked).toBe(false);
    expect(reader.timeLimit(false)).toBe(86400);
    expect(reader.isReasonRequired(false)).toBe(false);
    expect(reader.isSelfAddDisallowed(false)).toBe(false);
  });

  it('reports nothing applying for a group with no constraints', () => {
    const reader = carriedConstraints([]);
    expect(reader.timeLimit(true)).toBeNull();
    expect(reader.isReasonRequired(true)).toBe(false);
    expect(reader.isSelfAddDisallowed(true)).toBe(false);
  });

  it('treats an absent list as unknown rather than as nothing applying', () => {
    // The audit group reference and the create/update group responses do not
    // carry `effective_constraints` at all. Reading `undefined` as "no
    // constraints" hides the request button from an owner a self-add
    // restriction blocks -- taking away their only path -- so it fails closed.
    const reader = carriedConstraints(undefined);
    expect(reader.blocked).toBe(true);
    expect(reader.isReasonRequired(false)).toBe(true);
    expect(reader.isSelfAddDisallowed(false)).toBe(true);
    expect(reader.timeLimit(false)).toBeNull();
  });

  it('has no per-group map, so any row it is asked about is unknown', () => {
    // `carriedConstraints` wraps one group's own constraints; there is nothing
    // to answer a per-row question with, and unknown fails closed.
    const reader = carriedConstraints([entry('disallow_self_add_membership', true)]);
    expect(reader.isSelfAddDisallowed(false)).toBe(true);
    expect(reader.forGroup('some-other-group').isSelfAddDisallowed(false)).toBe(true);
  });
});

describe('approvalUntilDefault', () => {
  // An approval page renders once before its request has loaded, and React
  // Hook Form takes its defaults from that render. So the starting duration is
  // always written afterwards, and this decides what it should be.
  const asked = {requestedUntil: '2592000', requestedUntilAdjusted: undefined, timeLimit: null, autofillUntil: false};

  it('starts on the requested duration when no limit applies', () => {
    // The case that made this worth extracting: with no limit the narrowing
    // branch never runs, so anything that only narrowed would leave the form
    // on its pre-request snapshot -- which reads as indefinite. An approver
    // accepting that default grants unbounded access to a 30-day request.
    expect(approvalUntilDefault(asked)).toBe('2592000');
  });

  it('starts on the requested duration when it already fits the limit', () => {
    expect(approvalUntilDefault({...asked, timeLimit: 7776000, autofillUntil: true})).toBe('2592000');
  });

  it('moves to the longest option still on offer when the limit is narrower', () => {
    expect(
      approvalUntilDefault({...asked, requestedUntilAdjusted: '432000', timeLimit: 432000, autofillUntil: false}),
    ).toBe('432000');
  });

  it('leaves the field alone when the limit leaves nothing on offer', () => {
    // No option is short enough, so there is no valid duration to select.
    expect(
      approvalUntilDefault({...asked, requestedUntilAdjusted: undefined, timeLimit: 1, autofillUntil: false}),
    ).toBeNull();
  });

  it('carries an indefinite request through when nothing limits it', () => {
    expect(approvalUntilDefault({...asked, requestedUntil: 'indefinite'})).toBe('indefinite');
  });

  it('does not leave an indefinite request indefinite once a limit applies', () => {
    expect(
      approvalUntilDefault({
        ...asked,
        requestedUntil: 'indefinite',
        requestedUntilAdjusted: '43200',
        timeLimit: 43200,
        autofillUntil: false,
      }),
    ).toBe('43200');
  });
});
