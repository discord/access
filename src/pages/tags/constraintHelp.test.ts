import {describe, expect, it} from 'vitest';

import {
  CONSTRAINT_LABELS,
  DISALLOW_SELF_ADD_MEMBERSHIP,
  DISALLOW_SELF_ADD_OWNERSHIP,
  MEMBER_TIME_LIMIT,
  OWNER_TIME_LIMIT,
  REQUIRE_MEMBER_REASON,
  REQUIRE_OWNER_REASON,
  constraintEditHelp,
  constraintReadHelp,
  isConstraintInForce,
} from './constraintHelp';
import type {HelpParagraph} from './constraintHelp';

const KEYS = Object.keys(CONSTRAINT_LABELS);
const TIME_LIMITS = [MEMBER_TIME_LIMIT, OWNER_TIME_LIMIT];
const SELF_ADDS = [DISALLOW_SELF_ADD_MEMBERSHIP, DISALLOW_SELF_ADD_OWNERSHIP];
const BOOLEANS = [REQUIRE_MEMBER_REASON, REQUIRE_OWNER_REASON, ...SELF_ADDS];

function flatten(paragraphs: HelpParagraph[]): string {
  return paragraphs.map((p) => `${p.lead ?? ''}${p.text}`).join(' ');
}

function leads(paragraphs: HelpParagraph[]): string[] {
  return paragraphs.filter((p) => p.lead != null).map((p) => p.lead!);
}

describe('CONSTRAINT_LABELS', () => {
  it('covers all six constraints', () => {
    expect(KEYS).toHaveLength(6);
  });

  it('phrases every boolean as a question and neither time limit as one', () => {
    // The form answers each boolean with a Yes/No toggle, so the label reads as
    // the question that toggle answers. A time limit takes a number.
    for (const key of BOOLEANS) {
      expect(CONSTRAINT_LABELS[key]).toMatch(/\?$/);
    }
    for (const key of TIME_LIMITS) {
      expect(CONSTRAINT_LABELS[key]).not.toMatch(/\?$/);
    }
  });

  it('names the actor neutrally in the self-add labels', () => {
    // Not "owners": adding yourself to a role requires owning the *role*, not
    // the tagged group, so the restriction reaches people who own neither.
    for (const key of SELF_ADDS) {
      expect(CONSTRAINT_LABELS[key]).toContain('oneself');
      expect(CONSTRAINT_LABELS[key]).not.toContain('selves as');
    }
  });
});

describe('constraintEditHelp', () => {
  it('has copy for every constraint', () => {
    for (const key of KEYS) {
      expect(constraintEditHelp(key).length).toBeGreaterThan(0);
    }
  });

  it('describes both propagation cases for the four that can opt out', () => {
    for (const key of [MEMBER_TIME_LIMIT, OWNER_TIME_LIMIT, REQUIRE_MEMBER_REASON, REQUIRE_OWNER_REASON]) {
      expect(leads(constraintEditHelp(key))).toEqual(['With propagation to roles', 'Without propagation']);
    }
  });

  it('describes only the propagating case for the self-add pair, and says why', () => {
    // `#617` rejects "self-add restriction + propagation off" on every write, so
    // there is no second case to describe -- describing one would document a
    // configuration the backend refuses.
    for (const key of SELF_ADDS) {
      expect(leads(constraintEditHelp(key))).toEqual(['This constraint requires propagation to roles']);
      expect(flatten(constraintEditHelp(key))).not.toContain('Without propagation');
    }
  });

  it('carries no literal markdown, which would mean emphasis was pasted not split', () => {
    for (const key of KEYS) {
      for (const paragraph of constraintEditHelp(key)) {
        expect(paragraph.text).not.toContain('**');
        expect(paragraph.lead ?? '').not.toContain('**');
      }
    }
  });

  it('returns nothing for an unrecognised key', () => {
    expect(constraintEditHelp('not_a_constraint')).toEqual([]);
  });
});

describe('constraintReadHelp', () => {
  const forKey = (key: string, propagateToRoles: boolean) =>
    flatten(constraintReadHelp(key, {propagateToRoles, value: TIME_LIMITS.includes(key) ? 604800 : true}));

  it('describes different behaviour for each propagation state, for every constraint', () => {
    for (const key of KEYS) {
      expect(forKey(key, true)).not.toEqual(forKey(key, false));
    }
  });

  it('renders a time limit in days rather than seconds', () => {
    expect(forKey(MEMBER_TIME_LIMIT, true)).toContain('limited to 7 days');
    expect(constraintReadHelp(OWNER_TIME_LIMIT, {propagateToRoles: true, value: 86400})[0].text).toContain('1 day');
  });

  it('renders a sub-day limit as "<1 day" rather than rounding it to nothing', () => {
    // A one-hour limit is legal. Flooring to days would print "0 days", which
    // reads as no access at all.
    const text = constraintReadHelp(MEMBER_TIME_LIMIT, {propagateToRoles: true, value: 3600})[0].text;
    expect(text).toContain('<1 day');
    expect(text).not.toContain('0 days');
  });

  it('says the role roster is capped when propagating, and not when not', () => {
    expect(forKey(MEMBER_TIME_LIMIT, true)).toContain('Membership in those roles is limited to 7 days too');
    expect(forKey(MEMBER_TIME_LIMIT, false)).toContain('not limited by this tag');
  });

  it('crosses axes for the owner side', () => {
    // An owner limit reaches roles that OWN the group and lands on their
    // membership: being a member of the role is what confers the ownership.
    expect(forKey(OWNER_TIME_LIMIT, true)).toContain('Ownership of a group carrying this tag');
    expect(forKey(OWNER_TIME_LIMIT, true)).toContain('Membership in those roles');
    expect(forKey(REQUIRE_OWNER_REASON, true)).toContain('a role that owns such a group');
    expect(forKey(REQUIRE_MEMBER_REASON, true)).toContain("a role that's in such a group");
  });

  it('flags the illicit combination on a self-add restriction', () => {
    for (const key of SELF_ADDS) {
      expect(forKey(key, false)).toContain('should propagate to roles');
      expect(forKey(key, true)).not.toContain('should propagate to roles');
    }
  });

  it('says nothing for a flag that is switched off', () => {
    // Those rows are filtered out of the table, so there is nothing to explain.
    for (const key of BOOLEANS) {
      expect(constraintReadHelp(key, {propagateToRoles: true, value: false})).toEqual([]);
    }
  });

  it('returns nothing for an unrecognised key', () => {
    expect(constraintReadHelp('not_a_constraint', {propagateToRoles: true, value: true})).toEqual([]);
  });
});

describe('isConstraintInForce', () => {
  it('excludes a flag that is switched off', () => {
    expect(isConstraintInForce(false)).toBe(false);
  });

  it('includes a flag that is switched on', () => {
    expect(isConstraintInForce(true)).toBe(true);
  });

  it('includes a zero-second limit, which is the tightest one rather than none', () => {
    // Discriminated on `!== false`, not on falsiness. The API validator rejects
    // a zero limit, so this guards the helper rather than a reachable state.
    expect(isConstraintInForce(0)).toBe(true);
  });

  it('includes an ordinary limit', () => {
    expect(isConstraintInForce(604800)).toBe(true);
  });
});
