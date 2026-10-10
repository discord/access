import {describe, expect, it} from 'vitest';

import {CONSTRAINT_LABELS, DISALLOW_SELF_ADD_MEMBERSHIP, DISALLOW_SELF_ADD_OWNERSHIP} from '../../constraintCopy';
import {propagationConflictMessage, selfAddRestrictionAvailable} from './propagationRules';

// Asserted against the shared labels rather than hardcoded prose, so renaming a
// constraint cannot leave this passing while the message says something else.
const OWNER = CONSTRAINT_LABELS[DISALLOW_SELF_ADD_OWNERSHIP];
const MEMBER = CONSTRAINT_LABELS[DISALLOW_SELF_ADD_MEMBERSHIP];

describe('propagationConflictMessage', () => {
  it('returns null when propagation is on, whatever the self-add settings', () => {
    expect(propagationConflictMessage({propagateToRoles: 'yes', ownerAdd: true, memberAdd: true})).toBeNull();
  });

  it('returns null when propagation is off and no self-add restriction is set', () => {
    expect(propagationConflictMessage({propagateToRoles: 'no', ownerAdd: false, memberAdd: false})).toBeNull();
  });

  it('names the membership restriction when only it conflicts', () => {
    const message = propagationConflictMessage({propagateToRoles: 'no', ownerAdd: false, memberAdd: true});
    expect(message).toContain(MEMBER);
    expect(message).not.toContain(OWNER);
  });

  it('names the ownership restriction when only it conflicts', () => {
    const message = propagationConflictMessage({propagateToRoles: 'no', ownerAdd: true, memberAdd: false});
    expect(message).toContain(OWNER);
    expect(message).not.toContain(MEMBER);
  });

  it('names both restrictions when both conflict, since dropping one would still be invalid', () => {
    const message = propagationConflictMessage({propagateToRoles: 'no', ownerAdd: true, memberAdd: true});
    expect(message).toContain(OWNER);
    expect(message).toContain(MEMBER);
  });
});

// The form does not merely reject the forbidden pair, it refuses to let the
// user reach it: each control disables the option that would produce it. These
// walk every combination of the two self-add restrictions to check that the
// two halves of that rule agree, so no state exists in which both controls
// consider the move somebody else's job to block.
describe('reaching the forbidden combination', () => {
  const VALUES = [true, false] as const;

  for (const ownerAdd of VALUES) {
    for (const memberAdd of VALUES) {
      const restricted = ownerAdd || memberAdd;

      it(`narrowing the scope is ${restricted ? 'blocked' : 'allowed'} with ownerAdd=${ownerAdd} memberAdd=${memberAdd}`, () => {
        const conflict = propagationConflictMessage({propagateToRoles: 'no', ownerAdd, memberAdd});
        expect(conflict === null).toBe(!restricted);
      });
    }
  }

  it('offers the self-add restrictions only while the tag reaches roles', () => {
    expect(selfAddRestrictionAvailable('yes')).toBe(true);
    expect(selfAddRestrictionAvailable('no')).toBe(false);
  });

  it('treats an unset scope value as reaching roles, matching the form default', () => {
    expect(selfAddRestrictionAvailable(undefined)).toBe(true);
  });
});
