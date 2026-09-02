import dayjs from 'dayjs';
import {describe, expect, it} from 'vitest';

import {
  carriedConstraints,
  durationLabel,
  effectiveTimeLimit,
  isReasonRequired,
  isSelfAddDisallowed,
  untilOptionsFor,
} from './constraints';
import type {EffectiveConstraintDetail} from './api/apiSchemas';
import type {UntilOptions} from './constraints';

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

describe('durationLabel', () => {
  it('renders whole days, singular and plural', () => {
    expect(durationLabel(86400)).toBe('1 day');
    expect(durationLabel(604800)).toBe('7 days');
    expect(durationLabel(7776000)).toBe('90 days');
  });

  it('renders a sub-day limit exactly rather than rounding it away', () => {
    // A one-hour limit is a legal value -- the constraint validator only
    // requires a positive integer -- and it is offered as a duration the user
    // picks, so it has to say what it is.
    expect(durationLabel(3600)).toBe('1 hour');
    expect(durationLabel(1800)).toBe('30 minutes');
    expect(durationLabel(1)).toBe('1 second');
  });

  it('composes the units a value actually spans', () => {
    expect(durationLabel(5400)).toBe('1 hour and 30 minutes');
    expect(durationLabel(5401)).toBe('1 hour, 30 minutes, and 1 second');
    expect(durationLabel(90000)).toBe('1 day and 1 hour');
  });

  it('drops the units a value does not span', () => {
    expect(durationLabel(86401)).toBe('1 day and 1 second');
  });

  it('answers for a zero limit', () => {
    // `effectiveTimeLimit` treats a zero limit as a real constraint rather
    // than an absent one, so the formatter needs a value for it.
    expect(durationLabel(0)).toBe('0 seconds');
  });
});

// A fixed label map so these assert the helper's behavior rather than whatever
// `ACCESS_TIME_LABELS` an operator's `config.default.json` happens to carry.
const LABELS = {
  '43200': '12 Hours',
  '432000': '5 Days',
  '1209600': 'Two Weeks',
  indefinite: 'Indefinite',
  custom: 'Custom',
};

const ids = (result: UntilOptions) => result.options.map((option) => option.id);

describe('untilOptionsFor', () => {
  it('offers everything, indefinite included, when no limit applies', () => {
    const result = untilOptionsFor(null, {labels: LABELS});
    expect(ids(result)).toEqual(['43200', '432000', '1209600', 'indefinite', 'custom']);
  });

  it('offers every numeric option, but not indefinite, under a limit above them all', () => {
    const result = untilOptionsFor(2592000, {labels: LABELS, now: dayjs('2026-09-01T09:00:00')});
    expect(ids(result)).toEqual(['43200', '432000', '1209600', 'custom']);
    expect(result.longestId).toBe('1209600');
  });

  it('truncates at the longest option the limit permits', () => {
    const result = untilOptionsFor(500000, {labels: LABELS, now: dayjs('2026-09-01T09:00:00')});
    expect(ids(result)).toEqual(['43200', '432000', 'custom']);
    expect(result.longestId).toBe('432000');
  });

  it('synthesizes an option equal to the limit when no configured one fits', () => {
    // The dialogs are unsubmittable without this: an empty option list leaves
    // the required select with nothing to choose.
    const result = untilOptionsFor(3600, {labels: LABELS, now: dayjs('2026-09-01T09:00:00')});
    expect(result.options).toEqual([{id: '3600', label: '1 hour'}]);
    expect(result.longestId).toBe('3600');
  });

  it('gives the synthetic option an id the submit path can parse as seconds', () => {
    const result = untilOptionsFor(3600, {labels: LABELS, now: dayjs('2026-09-01T09:00:00')});
    expect(parseInt(result.longestId, 10)).toBe(3600);
  });

  it('always names a longest id, so no dialog defaults to undefined', () => {
    for (const limit of [null, 0, 1, 3600, 500000, 2592000]) {
      const result = untilOptionsFor(limit, {labels: LABELS, now: dayjs('2026-09-01T09:00:00')});
      expect(result.longestId).toBeDefined();
      expect(result.options.length).toBeGreaterThan(0);
    }
  });

  it('withholds custom when its date picker would have no selectable date', () => {
    // The picker disables every date on or before today and caps at
    // `now + timeLimit`, so a two-hour limit in the morning leaves nothing.
    const result = untilOptionsFor(7200, {labels: LABELS, now: dayjs('2026-09-01T01:00:00')});
    expect(ids(result)).not.toContain('custom');
  });

  it('offers custom when the same limit reaches into tomorrow', () => {
    const result = untilOptionsFor(7200, {labels: LABELS, now: dayjs('2026-09-01T23:00:00')});
    expect(ids(result)).toContain('custom');
  });
});
