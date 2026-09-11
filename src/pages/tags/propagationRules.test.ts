import {describe, expect, it} from 'vitest';

import {CONSTRAINT_LABELS, DISALLOW_SELF_ADD_MEMBERSHIP, DISALLOW_SELF_ADD_OWNERSHIP} from './constraintHelp';
import {propagationConflictMessage, selfAddRestrictionAvailable} from './propagationRules';

// Asserted against the shared labels rather than hardcoded prose, so renaming a
// constraint cannot leave this passing while the message says something else.
const OWNER = CONSTRAINT_LABELS[DISALLOW_SELF_ADD_OWNERSHIP].replace(/\?$/, '');
const MEMBER = CONSTRAINT_LABELS[DISALLOW_SELF_ADD_MEMBERSHIP].replace(/\?$/, '');

describe('propagationConflictMessage', () => {
  it('returns null when propagation is on, whatever the self-add settings', () => {
    expect(propagationConflictMessage({propagateToRoles: 'yes', ownerAdd: 'yes', memberAdd: 'yes'})).toBeNull();
  });

  it('returns null when propagation is off and no self-add restriction is set', () => {
    expect(propagationConflictMessage({propagateToRoles: 'no', ownerAdd: 'no', memberAdd: 'no'})).toBeNull();
  });

  it('names the membership restriction when only it conflicts', () => {
    const message = propagationConflictMessage({propagateToRoles: 'no', ownerAdd: 'no', memberAdd: 'yes'});
    expect(message).toContain(MEMBER);
    expect(message).not.toContain(OWNER);
  });

  it('names the ownership restriction when only it conflicts', () => {
    const message = propagationConflictMessage({propagateToRoles: 'no', ownerAdd: 'yes', memberAdd: 'no'});
    expect(message).toContain(OWNER);
    expect(message).not.toContain(MEMBER);
  });

  it('names both restrictions when both conflict, since dropping one would still be invalid', () => {
    const message = propagationConflictMessage({propagateToRoles: 'no', ownerAdd: 'yes', memberAdd: 'yes'});
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
  const VALUES = ['yes', 'no'] as const;

  for (const ownerAdd of VALUES) {
    for (const memberAdd of VALUES) {
      const restricted = ownerAdd === 'yes' || memberAdd === 'yes';

      it(`turning propagation off is ${restricted ? 'blocked' : 'allowed'} with ownerAdd=${ownerAdd} memberAdd=${memberAdd}`, () => {
        const conflict = propagationConflictMessage({propagateToRoles: 'no', ownerAdd, memberAdd});
        expect(conflict === null).toBe(!restricted);
      });
    }
  }

  it('offers the self-add restrictions only while propagation is on', () => {
    expect(selfAddRestrictionAvailable('yes')).toBe(true);
    expect(selfAddRestrictionAvailable('no')).toBe(false);
  });

  it('treats an unset propagation value as on, matching the form default', () => {
    expect(selfAddRestrictionAvailable(undefined)).toBe(true);
  });
});
