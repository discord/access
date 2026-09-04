import {describe, expect, it} from 'vitest';

import {CONSTRAINT_LABELS, DISALLOW_SELF_ADD_MEMBERSHIP, DISALLOW_SELF_ADD_OWNERSHIP} from './constraintHelp';
import {propagationConflictMessage} from './propagationRules';

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
