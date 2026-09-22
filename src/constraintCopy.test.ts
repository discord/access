import {describe, expect, it} from 'vitest';

import {
  CONSTRAINT_LABELS,
  CONSTRAINT_ORDER,
  DISALLOW_SELF_ADD_MEMBERSHIP,
  MEMBER_TIME_LIMIT,
  OWNER_TIME_LIMIT,
  REQUIRE_MEMBER_REASON,
  byConstraintOrder,
  constraintDetail,
  constraintLabel,
  constraintSummary,
  isConstraintInForce,
} from './constraintCopy';

describe('constraintLabel', () => {
  it('names every constraint the app knows about', () => {
    for (const key of CONSTRAINT_ORDER) {
      expect(constraintLabel(key)).toBe(CONSTRAINT_LABELS[key]);
    }
  });

  it('falls back to the key for a constraint this build has no copy for', () => {
    // A backend ahead of the deployed frontend. The key at least says which
    // setting is in force; a bare lookup would render nothing.
    expect(constraintLabel('some_future_constraint')).toBe('some_future_constraint');
  });

  it('gives no label a trailing question mark or colon', () => {
    // Punctuation belongs at the render site: `propagationRules` quotes these
    // mid-sentence, and the form appends its own separator.
    for (const key of CONSTRAINT_ORDER) {
      expect(constraintLabel(key)).not.toMatch(/[?:]$/);
    }
  });
});

describe('constraintSummary', () => {
  it('says something different depending on whether the tag reaches roles', () => {
    for (const key of CONSTRAINT_ORDER) {
      const reaching = constraintSummary(key, true);
      const narrow = constraintSummary(key, false);
      expect(reaching).not.toBe('');
      expect(narrow).not.toBe('');
      expect(reaching).not.toBe(narrow);
    }
  });

  it('tells a reader the narrow scope makes a self-add restriction unavailable', () => {
    expect(constraintSummary(DISALLOW_SELF_ADD_MEMBERSHIP, false)).toMatch(/[Uu]navailable/);
  });

  it('returns nothing for an unrecognised constraint rather than a stray gap', () => {
    expect(constraintSummary('some_future_constraint', true)).toBe('');
  });
});

describe('constraintDetail', () => {
  it('closes with a paragraph about the scope in force, whichever it is', () => {
    for (const key of CONSTRAINT_ORDER) {
      for (const propagates of [true, false]) {
        const paragraphs = constraintDetail(key, propagates);
        expect(paragraphs.length).toBeGreaterThan(1);
        expect(paragraphs[paragraphs.length - 1].lead).toBeTruthy();
      }
    }
  });

  it('describes the two scopes differently', () => {
    const reaching = constraintDetail(MEMBER_TIME_LIMIT, true);
    const narrow = constraintDetail(MEMBER_TIME_LIMIT, false);
    expect(reaching[reaching.length - 1].text).not.toBe(narrow[narrow.length - 1].text);
  });

  it('returns nothing for an unrecognised constraint', () => {
    expect(constraintDetail('some_future_constraint', true)).toEqual([]);
  });
});

describe('byConstraintOrder', () => {
  it('puts the six in a fixed order regardless of how they arrive', () => {
    const shuffled = [...CONSTRAINT_ORDER].reverse();
    expect(shuffled.sort(byConstraintOrder)).toEqual(CONSTRAINT_ORDER);
  });

  it('sorts an unrecognised constraint last rather than dropping it', () => {
    const sorted = ['some_future_constraint', MEMBER_TIME_LIMIT].sort(byConstraintOrder);
    expect(sorted).toEqual([MEMBER_TIME_LIMIT, 'some_future_constraint']);
  });

  it('leads each pair with the membership side', () => {
    expect(CONSTRAINT_ORDER.indexOf(MEMBER_TIME_LIMIT)).toBeLessThan(CONSTRAINT_ORDER.indexOf(OWNER_TIME_LIMIT));
  });
});

describe('isConstraintInForce', () => {
  it('drops a flag that is switched off', () => {
    expect(isConstraintInForce(false)).toBe(false);
  });

  it('keeps a zero-second limit, which is the tightest one rather than the absence of one', () => {
    expect(isConstraintInForce(0)).toBe(true);
  });

  it('keeps a flag that is switched on and an ordinary limit', () => {
    expect(isConstraintInForce(true)).toBe(true);
    expect(isConstraintInForce(604800)).toBe(true);
  });
});
